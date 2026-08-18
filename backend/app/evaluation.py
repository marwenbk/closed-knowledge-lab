from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from statistics import mean
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from sqlalchemy import Engine

from app.answering import (
    AnswerabilityStatus,
    GroundedAnswer,
    answer_knowledge,
    contextualize_query,
)
from app.config import PROJECT_ROOT, Settings
from app.embeddings import EmbeddingProvider
from app.kb import normalize_content
from app.llm import LLMProvider
from app.retrieval import RetrievalMatch, RetrievalResult, retrieve_knowledge

DEFAULT_CASES_PATH = PROJECT_ROOT / "evals" / "cases.yaml"
DEFAULT_FACTS_PATH = PROJECT_ROOT / "data" / "fact_catalog.yaml"
DEFAULT_FIXTURES_PATH = PROJECT_ROOT / "data" / "conflict_fixtures.yaml"
SUPPORTED_STATUSES = {"ANSWERABLE", "PARTIALLY_ANSWERABLE"}


class EvaluationError(RuntimeError):
    pass


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectedDocuments(_Contract):
    canonical: tuple[str, ...]
    acceptable: tuple[str, ...]


class EvaluationCase(_Contract):
    id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    suite: Literal["retrieval", "pipeline", "adversarial"]
    category: str = Field(min_length=1)
    language: str = Field(min_length=1)
    messages: tuple[str, ...] = Field(min_length=1)
    expected_status: AnswerabilityStatus
    required_fact_ids: tuple[str, ...]
    forbidden_fact_ids: tuple[str, ...]
    expected_documents: ExpectedDocuments
    required_phrases: tuple[str, ...] = ()
    clarification_intent: str | None = None
    requires_fixture: bool = False
    fixture_id: str | None = None

    @model_validator(mode="after")
    def validate_fixture_reference(self) -> EvaluationCase:
        if self.requires_fixture != (self.fixture_id is not None):
            raise ValueError("fixture_id and requires_fixture must be declared together")
        return self


class Fact(_Contract):
    canonical_document: str
    canonical_section: str
    acceptable_documents: tuple[str, ...]
    expected_fragment: str
    policy_id: str | None
    tags: tuple[str, ...]


class FactCatalog(_Contract):
    schema_version: str
    dataset_id: str
    dataset_version: str
    facts: dict[str, Fact]


class FixtureDocument(_Contract):
    document_id: str
    title: str
    content: str


class ConflictFixture(_Contract):
    id: str
    namespace: str
    conflicts_with_fact_id: str
    document: FixtureDocument


class FixtureCatalog(_Contract):
    schema_version: str
    dataset_id: str
    dataset_version: str
    fixtures: tuple[ConflictFixture, ...]


class EvaluationData(_Contract):
    cases: tuple[EvaluationCase, ...]
    catalog: FactCatalog
    fixtures: dict[str, ConflictFixture]


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EvaluationError(f"Could not load evaluation data: {path}") from exc


def load_evaluation_data(
    cases_path: Path = DEFAULT_CASES_PATH,
    facts_path: Path = DEFAULT_FACTS_PATH,
    fixtures_path: Path = DEFAULT_FIXTURES_PATH,
) -> EvaluationData:
    try:
        cases = tuple(TypeAdapter(list[EvaluationCase]).validate_python(_load_yaml(cases_path)))
        catalog = FactCatalog.model_validate(_load_yaml(facts_path))
        fixture_catalog = FixtureCatalog.model_validate(_load_yaml(fixtures_path))
    except ValueError as exc:
        raise EvaluationError(f"Invalid evaluation contract: {exc}") from exc

    if len(cases) < 95 or len({case.id for case in cases}) != len(cases):
        raise EvaluationError("Evaluation cases must contain at least 95 unique IDs")
    if (
        fixture_catalog.schema_version != catalog.schema_version
        or fixture_catalog.dataset_id != catalog.dataset_id
        or fixture_catalog.dataset_version != catalog.dataset_version
    ):
        raise EvaluationError("Fact and fixture catalogs must describe the same dataset")

    fixtures = {fixture.id: fixture for fixture in fixture_catalog.fixtures}
    if len(fixtures) != len(fixture_catalog.fixtures):
        raise EvaluationError("Conflict fixture IDs must be unique")
    for fixture in fixtures.values():
        if fixture.conflicts_with_fact_id not in catalog.facts:
            raise EvaluationError(f"Fixture {fixture.id} references an unknown fact")
        if not fixture.namespace.startswith("eval-conflict-"):
            raise EvaluationError(f"Fixture {fixture.id} is not isolated")

    for case in cases:
        if (case.dataset_id, case.dataset_version) != (
            catalog.dataset_id,
            catalog.dataset_version,
        ):
            raise EvaluationError(f"Case {case.id} targets a different dataset")
        referenced = {*case.required_fact_ids, *case.forbidden_fact_ids}
        unknown = referenced - catalog.facts.keys()
        if unknown:
            raise EvaluationError(f"Case {case.id} references unknown facts: {sorted(unknown)}")
        if set(case.required_fact_ids) & set(case.forbidden_fact_ids):
            raise EvaluationError(f"Case {case.id} requires and forbids the same fact")
        canonical = tuple(
            sorted(
                {catalog.facts[fact_id].canonical_document for fact_id in case.required_fact_ids}
            )
        )
        acceptable = tuple(
            sorted(
                {
                    document
                    for fact_id in case.required_fact_ids
                    for document in catalog.facts[fact_id].acceptable_documents
                    if document not in canonical
                }
            )
        )
        if case.expected_documents != ExpectedDocuments(canonical=canonical, acceptable=acceptable):
            raise EvaluationError(f"Case {case.id} has stale expected documents")
        if case.fixture_id is not None and case.fixture_id not in fixtures:
            raise EvaluationError(f"Case {case.id} references an unknown conflict fixture")
        if case.fixture_id is not None and case.expected_status != "CONFLICTING_EVIDENCE":
            raise EvaluationError(f"Fixture case {case.id} must expect conflicting evidence")
    return EvaluationData(cases=cases, catalog=catalog, fixtures=fixtures)


def _case_query(case: EvaluationCase) -> str:
    return contextualize_query(case.messages[-1], case.messages[:-1])


def _requires_second_hop(case: EvaluationCase) -> bool:
    documents = set(case.expected_documents.canonical)
    return (
        case.category in {"multi_document", "ambiguity_follow_up"}
        and "employer-plans" in documents
        and len(documents) > 1
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _category_metrics(
    cases: tuple[EvaluationCase, ...], results: list[dict[str, Any]]
) -> dict[str, Any]:
    counts = Counter(case.category for case in cases)
    metrics: dict[str, Any] = {}
    for category, case_count in sorted(counts.items()):
        group = [item for item in results if item["category"] == category]
        passed = sum(item["passed"] for item in group)
        metrics[category] = {
            "case_count": case_count,
            "evaluated": len(group),
            "passed": passed,
            "pass_rate": _ratio(passed, len(group)),
        }
    return metrics


def evaluate_retrieval_results(
    data: EvaluationData,
    results: dict[str, RetrievalResult],
    *,
    k: int,
) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    source_hits = fact_hits = expected_sources = expected_facts = 0
    reciprocal_ranks: list[float] = []
    second_hop_total = second_hop_hits = 0

    for case in data.cases:
        if case.requires_fixture or not case.required_fact_ids:
            continue
        result = results[case.id]
        matches = result.matches[:k]
        documents = [match.document_key for match in matches]
        expected = set(case.expected_documents.canonical)
        missing_documents = sorted(expected - set(documents))
        found_facts = [
            fact_id
            for fact_id in case.required_fact_ids
            if any(
                normalize_content(data.catalog.facts[fact_id].expected_fragment)
                in normalize_content(match.content)
                for match in matches
            )
        ]
        missing_facts = sorted(set(case.required_fact_ids) - set(found_facts))
        needs_second_hop = _requires_second_hop(case)
        second_hop_ok = not needs_second_hop or result.second_hop_query is not None
        provenance_ok = (result.dataset_id, result.dataset_version) == (
            case.dataset_id,
            case.dataset_version,
        )
        if needs_second_hop:
            second_hop_total += 1
            second_hop_hits += second_hop_ok

        for document in expected:
            expected_sources += 1
            if document in documents:
                source_hits += 1
                reciprocal_ranks.append(1 / (documents.index(document) + 1))
            else:
                reciprocal_ranks.append(0.0)
        expected_facts += len(case.required_fact_ids)
        fact_hits += len(found_facts)
        passed = not missing_documents and not missing_facts and second_hop_ok and provenance_ok
        details.append(
            {
                "id": case.id,
                "suite": case.suite,
                "category": case.category,
                "passed": passed,
                "missing_documents": missing_documents,
                "missing_fact_ids": missing_facts,
                "retrieved_documents": documents,
                "second_hop_required": needs_second_hop,
                "second_hop_used": result.second_hop_query is not None,
                "provenance_ok": provenance_ok,
                "trigram_fallback_used": result.trigram_fallback_used,
                "duration_ms": result.duration_ms,
            }
        )

    passed = all(item["passed"] for item in details)
    return {
        "status": "passed" if passed else "failed",
        "k": k,
        "evaluated_cases": len(details),
        "passed_cases": sum(item["passed"] for item in details),
        "source_recall_at_k": _ratio(source_hits, expected_sources),
        "fact_recall_at_k": _ratio(fact_hits, expected_facts),
        "mean_reciprocal_rank": round(mean(reciprocal_ranks), 4) if reciprocal_ranks else None,
        "second_hop_coverage": _ratio(second_hop_hits, second_hop_total),
        "typo_fallback_cases": sum(
            item["trigram_fallback_used"]
            for item in details
            if item["category"] == "typo_paraphrase"
        ),
        "categories": _category_metrics(data.cases, details),
        "cases": details,
    }


def evaluate_answer_results(
    data: EvaluationData, results: dict[str, GroundedAnswer]
) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    for case in data.cases:
        result = results[case.id]
        cited_documents = {citation.document_key for citation in result.citations}
        supported = result.status in SUPPORTED_STATUSES
        missing_documents = (
            sorted(set(case.expected_documents.canonical) - cited_documents) if supported else []
        )
        forbidden = [
            fact_id
            for fact_id in case.forbidden_fact_ids
            if any(
                normalize_content(data.catalog.facts[fact_id].expected_fragment)
                in normalize_content(citation.quote)
                for citation in result.citations
            )
        ]
        verification_ok = (
            result.verification_status == "VERIFIED" and bool(result.citations)
            if supported
            else result.verification_status == "NOT_REQUIRED" and not result.citations
        )
        clarification_ok = case.clarification_intent is None or (
            result.status == "AMBIGUOUS" and bool(result.answer.strip())
        )
        provenance_ok = (result.dataset_id, result.dataset_version) == (
            case.dataset_id,
            case.dataset_version,
        )
        passed = (
            result.status == case.expected_status
            and not missing_documents
            and not forbidden
            and verification_ok
            and clarification_ok
            and provenance_ok
        )
        details.append(
            {
                "id": case.id,
                "suite": case.suite,
                "category": case.category,
                "passed": passed,
                "expected_status": case.expected_status,
                "actual_status": result.status,
                "missing_documents": missing_documents,
                "forbidden_fact_ids_cited": forbidden,
                "verification_status": result.verification_status,
                "clarification_ok": clarification_ok,
                "provenance_ok": provenance_ok,
                "duration_ms": result.duration_ms,
            }
        )

    passed = all(item["passed"] for item in details)
    return {
        "status": "passed" if passed else "failed",
        "evaluated_cases": len(details),
        "passed_cases": sum(item["passed"] for item in details),
        "categories": _category_metrics(data.cases, details),
        "cases": details,
    }


def _with_fixture(
    result: RetrievalResult, fixture: ConflictFixture, canonical_fact: Fact
) -> RetrievalResult:
    canonical_match = next(
        (
            match
            for match in result.matches
            if normalize_content(canonical_fact.expected_fragment)
            in normalize_content(match.content)
        ),
        None,
    )
    if canonical_match is None:
        raise EvaluationError(
            f"Canonical evidence was not retrieved for conflict fixture {fixture.id}"
        )
    match = RetrievalMatch(
        chunk_id=uuid5(NAMESPACE_URL, fixture.namespace),
        stable_chunk_key=f"{fixture.document.document_id}__evaluation__001",
        document_key=fixture.document.document_id,
        document_title=fixture.document.title,
        source_path=f"data/conflict_fixtures.yaml#{fixture.id}",
        section="Isolated conflict fixture",
        section_path=(fixture.document.title,),
        ordinal=1,
        content=fixture.document.content,
        rrf_score=1.0,
        signals={"fixture": {"rank": 1, "score": 1.0}},
    )
    remaining = [item for item in result.matches if item.chunk_id != canonical_match.chunk_id]
    return replace(result, matches=(match, canonical_match, *remaining[:4]))


def run_evaluation(
    engine: Engine,
    embedding_provider: EmbeddingProvider,
    settings: Settings,
    data: EvaluationData,
    *,
    llm_provider: LLMProvider | None = None,
    kb_version_id: UUID | None = None,
) -> dict[str, Any]:
    cases_to_retrieve = (
        data.cases
        if llm_provider is not None
        else tuple(
            case for case in data.cases if case.required_fact_ids and not case.requires_fixture
        )
    )
    retrievals = {
        case.id: retrieve_knowledge(
            engine,
            embedding_provider,
            settings,
            _case_query(case),
            kb_version_id=kb_version_id,
        )
        for case in cases_to_retrieve
    }
    retrieval_report = evaluate_retrieval_results(data, retrievals, k=settings.final_context_k)

    answer_report: dict[str, Any] = {"status": "not_run", "evaluated_cases": 0}
    if llm_provider is not None:
        answers: dict[str, GroundedAnswer] = {}
        for case in data.cases:
            retrieval = retrievals[case.id]
            if case.fixture_id is not None:
                fixture = data.fixtures[case.fixture_id]
                retrieval = _with_fixture(
                    retrieval,
                    fixture,
                    data.catalog.facts[fixture.conflicts_with_fact_id],
                )
            answers[case.id] = answer_knowledge(
                engine,
                embedding_provider,
                llm_provider,
                settings,
                case.messages[-1],
                conversation_context=case.messages[:-1],
                retrieval_result=retrieval,
            )
        answer_report = evaluate_answer_results(data, answers)

    contract = {
        "status": "passed",
        "total_cases": len(data.cases),
        "suite_counts": dict(sorted(Counter(case.suite for case in data.cases).items())),
        "category_counts": dict(sorted(Counter(case.category for case in data.cases).items())),
        "fixture_cases": sum(case.requires_fixture for case in data.cases),
    }
    passed = retrieval_report["status"] == "passed" and (
        llm_provider is None or answer_report["status"] == "passed"
    )
    return {
        "schema_version": "1.0.0",
        "mode": "full" if llm_provider is not None else "retrieval",
        "dataset_id": data.catalog.dataset_id,
        "dataset_version": data.catalog.dataset_version,
        "passed": passed,
        "contract": contract,
        "retrieval": retrieval_report,
        "answering": answer_report,
    }


def write_report(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
