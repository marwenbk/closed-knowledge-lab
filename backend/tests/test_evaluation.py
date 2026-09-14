from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Literal
from uuid import UUID

import pytest
import yaml
from app.answering import AnswerabilityStatus, Citation, GroundedAnswer, ModelIdentity
from app.config import Settings
from app.embeddings import (
    EmbeddingError,
    configured_embedding_provider,
    embed_knowledge_base,
    ensure_configured_model_artifacts,
    ensure_model_artifacts,
)
from app.evaluation import (
    EvaluationData,
    EvaluationError,
    _with_fixture,
    evaluate_answer_results,
    evaluate_retrieval_results,
    load_evaluation_data,
    run_evaluation,
    write_report,
)
from app.kb import activate_knowledge_base, import_knowledge_base
from app.retrieval import RetrievalMatch, RetrievalResult
from app.tuning import load_runtime_snapshot
from sqlalchemy import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "knowledge_base" / "manifest.json"


def _match(number: int, document: str, content: str) -> RetrievalMatch:
    return RetrievalMatch(
        chunk_id=UUID(int=number),
        stable_chunk_key=f"{document}__evaluation__{number:03d}",
        document_key=document,
        document_title=document,
        source_path=f"knowledge_base/{document}.md",
        section="Evaluation",
        section_path=("Evaluation",),
        ordinal=number,
        content=content,
        rrf_score=1 / number,
        signals={"test": {"rank": number, "score": 1.0}},
    )


def _retrieval(data: EvaluationData, case_id: str) -> RetrievalResult:
    case = next(case for case in data.cases if case.id == case_id)
    fragments: dict[str, list[str]] = defaultdict(list)
    for fact_id in case.required_fact_ids:
        fact = data.catalog.facts[fact_id]
        fragments[fact.canonical_document].append(fact.expected_fragment)
    matches = tuple(
        _match(index, document, "\n".join(content))
        for index, (document, content) in enumerate(fragments.items(), start=1)
    )
    return RetrievalResult(
        query=case.messages[-1],
        dataset_id=case.dataset_id,
        dataset_version=case.dataset_version,
        embedding_model="test/embedding",
        embedding_version="a" * 40,
        matches=matches,
        trigram_fallback_used=case.category == "typo_paraphrase",
        second_hop_query="mapped plan" if len(matches) > 1 else None,
        duration_ms=1.0,
    )


def _answer(
    status: AnswerabilityStatus,
    *,
    citation: Citation | None = None,
) -> GroundedAnswer:
    return GroundedAnswer(
        status=status,
        answer="Evaluation response.",
        citations=(citation,) if citation else (),
        dataset_id="topmed-demo",
        dataset_version="3.0.0",
        model=ModelIdentity(
            provider="test",
            name="test/model",
            version="test/model",
            prompt_version="1.0.0",
        ),
        verification_status="VERIFIED" if citation else "NOT_REQUIRED",
        regenerated=False,
        duration_ms=1.0,
    )


def test_loads_the_complete_versioned_evaluation_contract() -> None:
    data = load_evaluation_data()

    assert len(data.cases) == 100
    assert len(data.catalog.facts) == 76
    assert len(data.fixtures) == 5
    assert {case.suite for case in data.cases} == {"retrieval", "pipeline", "adversarial"}


def test_rejects_stale_expected_documents(tmp_path: Path) -> None:
    cases = yaml.safe_load((PROJECT_ROOT / "evals/cases.yaml").read_text(encoding="utf-8"))
    cases[0]["expected_documents"]["canonical"] = ["wrong-document"]
    path = tmp_path / "cases.yaml"
    path.write_text(yaml.safe_dump(cases, allow_unicode=True), encoding="utf-8")

    with pytest.raises(EvaluationError, match="stale expected documents"):
        load_evaluation_data(cases_path=path)


def test_retrieval_gate_checks_sources_facts_second_hop_and_categories() -> None:
    full_data = load_evaluation_data()
    cases = tuple(
        case
        for case in full_data.cases
        if case.id in {"direct_family_dependents_001", "multi_gold_dependents_001"}
    )
    data = full_data.model_copy(update={"cases": cases})
    results = {case.id: _retrieval(data, case.id) for case in cases}

    report = evaluate_retrieval_results(data, results, k=6)

    assert report["status"] == "passed"
    assert report["source_recall_at_k"] == 1.0
    assert report["fact_recall_at_k"] == 1.0
    assert report["second_hop_coverage"] == 1.0
    assert report["categories"]["multi_document"]["pass_rate"] == 1.0

    results["direct_family_dependents_001"] = replace(
        _retrieval(data, "direct_family_dependents_001"), matches=()
    )
    failed = evaluate_retrieval_results(data, results, k=6)
    assert failed["status"] == "failed"
    assert failed["source_recall_at_k"] < 1.0


def test_answer_gate_checks_status_citations_and_writes_json(tmp_path: Path) -> None:
    full_data = load_evaluation_data()
    direct = next(case for case in full_data.cases if case.id == "direct_family_dependents_001")
    missing = next(case for case in full_data.cases if case.id == "missing_information_001")
    data = full_data.model_copy(update={"cases": (direct, missing)})
    citation = Citation(
        citation_id="c1",
        chunk_id=UUID(int=1),
        stable_chunk_key="family-members__evaluation__001",
        document_key="family-members",
        document="Family",
        section="Limits",
        quote=data.catalog.facts["PLAN_FAMILY_MAX_DEPENDENTS"].expected_fragment,
    )

    report = evaluate_answer_results(
        data,
        {
            direct.id: _answer("ANSWERABLE", citation=citation),
            missing.id: _answer("NOT_ANSWERABLE"),
        },
    )
    output = tmp_path / "evaluation.report.json"
    write_report({"answering": report}, output)

    assert report["status"] == "passed"
    assert report["categories"]["direct_answer"]["pass_rate"] == 1.0
    assert '"status": "passed"' in output.read_text(encoding="utf-8")


def test_conflict_overlay_preserves_canonical_and_fixture_evidence() -> None:
    data = load_evaluation_data()
    fixture = data.fixtures["conflict_family_dependents"]
    fact = data.catalog.facts[fixture.conflicts_with_fact_id]
    base = _retrieval(data, "direct_family_dependents_001")

    result = _with_fixture(base, fixture, fact)

    assert len(result.matches) <= 6
    assert result.matches[0].source_path.startswith("data/conflict_fixtures.yaml")
    assert fact.expected_fragment in result.matches[1].content


@pytest.mark.model
@pytest.mark.postgres
@pytest.mark.parametrize("embedding_provider", ["onnx", "static"])
def test_generated_retrieval_gate_with_postgres_and_pinned_model(
    postgres_engine: Engine,
    embedding_provider: Literal["onnx", "static"],
) -> None:
    settings = Settings(embedding_provider=embedding_provider)
    try:
        if embedding_provider == "onnx":
            ensure_model_artifacts(settings)
        else:
            ensure_configured_model_artifacts(settings)
    except EmbeddingError as exc:
        if "is missing" in str(exc):
            if os.environ.get("TOPMED_REQUIRE_MODEL_TESTS") == "1":
                pytest.fail(f"Pinned embedding model is required but unavailable: {exc}")
            pytest.skip("Pinned embedding model has not been downloaded by backend setup")
        raise

    provider = configured_embedding_provider(settings)
    import_knowledge_base(postgres_engine, MANIFEST_PATH)
    embed_knowledge_base(
        postgres_engine,
        provider,
        batch_size=settings.embedding_batch_size,
        dataset_id=settings.expected_dataset_id,
        dataset_version=settings.expected_dataset_version,
    )
    activate_knowledge_base(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        dataset_version=settings.expected_dataset_version,
        expected_embedding_model=provider.model_id,
        expected_embedding_version=provider.model_version,
        expected_embedding_dimensions=provider.dimensions,
    )

    runtime = load_runtime_snapshot(postgres_engine, settings)
    report = run_evaluation(
        postgres_engine,
        provider,
        runtime.effective_settings,
        runtime.prompts,
        load_evaluation_data(),
    )

    failed_cases = [case["id"] for case in report["retrieval"]["cases"] if not case["passed"]]
    assert report["passed"] is True, failed_cases
    assert report["retrieval"]["evaluated_cases"] == 63
    assert report["retrieval"]["source_recall_at_k"] == 1.0
    assert report["retrieval"]["fact_recall_at_k"] == 1.0
    assert report["retrieval"]["second_hop_coverage"] == 1.0
