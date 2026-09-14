from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import pytest
from app import answering
from app.answering import (
    CONFLICT_ANSWER,
    LIMITATION_ANSWER,
    AnswerabilityDecision,
    AnswerabilityStatus,
    AnswerDraft,
    AnsweringError,
    DraftClaim,
    EvidenceReference,
    VerificationDecision,
    answer_knowledge,
    answer_knowledge_with_trace,
)
from app.config import Settings
from app.retrieval import RetrievalMatch, RetrievalResult
from app.tuning import PromptBundle
from pydantic import BaseModel

PROMPTS = PromptBundle(
    answerability_prompt="Classify using only the supplied evidence. " * 3,
    generation_prompt="Answer only from the evidence and cite every claim. " * 3,
    verification_prompt="Verify every claim only against the evidence. " * 3,
)


class StubLLMProvider:
    provider_id = "test"
    model_id = "deepseek:test"
    model_version = "deepseek:test"

    def __init__(self, responses: Sequence[BaseModel]) -> None:
        self.responses = list(responses)

    def ensure_ready(self) -> str:
        return self.model_version

    def structured_generate(
        self,
        _messages: Sequence[Mapping[str, str]],
        response_model: type[BaseModel],
    ) -> Any:
        response = self.responses.pop(0)
        assert isinstance(response, response_model)
        return response

    def close(self) -> None:
        pass


def _match() -> RetrievalMatch:
    return RetrievalMatch(
        chunk_id=UUID(int=1),
        stable_chunk_key="family-members__limits__001",
        document_key="family-members",
        document_title="Family members and dependents",
        source_path="knowledge_base/05-family-members.md",
        section="Dependent limits",
        section_path=("Family members and dependents", "Dependent limits"),
        ordinal=1,
        content="The Family plan allows **up to 3 dependents**.",
        rrf_score=1.0,
        signals={},
    )


def _retrieval() -> RetrievalResult:
    return RetrievalResult(
        query="How many dependents does the Family plan allow?",
        dataset_id="topmed-demo",
        dataset_version="3.0.0",
        embedding_model="test/embedding",
        embedding_version="a" * 40,
        matches=(_match(),),
        trigram_fallback_used=False,
        second_hop_query=None,
        duration_ms=1.0,
    )


def _multi_hop_retrieval() -> RetrievalResult:
    mapping = RetrievalMatch(
        chunk_id=UUID(int=2),
        stable_chunk_key="employer-plans__mapping__001",
        document_key="employer-plans",
        document_title="Planos empresariais",
        source_path="knowledge_base/03-employer-plans.md",
        section="Gold",
        section_path=("Planos empresariais", "Gold"),
        ordinal=1,
        content="The Gold tier maps to the Family plan.",
        rrf_score=1.0,
        signals={},
    )
    return RetrievalResult(
        query="Tenho Gold. Quantos dependentes posso cadastrar?",
        dataset_id="topmed-demo",
        dataset_version="3.0.0",
        embedding_model="test/embedding",
        embedding_version="a" * 40,
        matches=(mapping, _match()),
        trigram_fallback_used=False,
        second_hop_query="dependent limit for the Family plan",
        duration_ms=1.0,
    )


def _draft(quote: str) -> AnswerDraft:
    return AnswerDraft(
        answer="The Family plan allows up to 3 dependents.",
        claims=[
            DraftClaim(
                text="The Family plan allows up to 3 dependents.",
                evidence=[EvidenceReference(chunk_id=str(UUID(int=1)), quote=quote)],
            )
        ],
    )


def _run(monkeypatch: pytest.MonkeyPatch, provider: StubLLMProvider) -> Any:
    monkeypatch.setattr(answering, "retrieve_knowledge", lambda *_: _retrieval())
    return answer_knowledge(
        object(),
        object(),
        provider,
        Settings(_env_file=None),
        PROMPTS,
        "How many dependents does the Family plan allow?",
    )


def test_answer_pipeline_returns_only_verified_exact_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = StubLLMProvider(
        [
            AnswerabilityDecision(
                status="ANSWERABLE",
                selected_chunk_ids=[str(UUID(int=1))],
                unsupported_aspects=[],
            ),
            _draft("The Family plan allows up to 3 dependents."),
            VerificationDecision(supported=True, issues=[]),
        ]
    )

    result = _run(monkeypatch, provider)

    assert result.status == "ANSWERABLE"
    assert result.verification_status == "VERIFIED"
    assert result.regenerated is False
    assert result.citations[0].citation_id == "c1"
    assert result.citations[0].stable_chunk_key == "family-members__limits__001"
    assert provider.responses == []


def test_answer_pipeline_exposes_only_structured_operational_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = StubLLMProvider(
        [
            AnswerabilityDecision(
                status="ANSWERABLE",
                selected_chunk_ids=[str(UUID(int=1))],
                unsupported_aspects=[],
            ),
            _draft("The Family plan allows up to 3 dependents."),
            VerificationDecision(supported=True, issues=[]),
        ]
    )
    monkeypatch.setattr(answering, "retrieve_knowledge", lambda *_: _retrieval())

    execution = answer_knowledge_with_trace(
        object(),
        object(),
        provider,
        Settings(_env_file=None),
        PROMPTS,
        "How many dependents does the Family plan allow?",
    )

    assert execution.answer.verification_status == "VERIFIED"
    assert execution.trace["selected_chunk_ids"] == [str(UUID(int=1))]
    assert execution.trace["attempts"][0]["citation_validation"]["valid"] is True
    assert "reasoning" not in execution.trace


@pytest.mark.parametrize(
    ("status", "expected_answer"),
    [("NOT_ANSWERABLE", LIMITATION_ANSWER), ("CONFLICTING_EVIDENCE", CONFLICT_ANSWER)],
)
def test_unsupported_or_conflicting_request_stops_before_generation(
    monkeypatch: pytest.MonkeyPatch,
    status: AnswerabilityStatus,
    expected_answer: str,
) -> None:
    provider = StubLLMProvider(
        [
            AnswerabilityDecision(
                status=status,
                selected_chunk_ids=[],
                unsupported_aspects=[],
            )
        ]
    )

    result = _run(monkeypatch, provider)

    assert result.status == status
    assert result.answer == expected_answer
    assert result.citations == ()
    assert result.verification_status == "NOT_REQUIRED"
    assert provider.responses == []


def test_invalid_citation_is_regenerated_once(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = StubLLMProvider(
        [
            AnswerabilityDecision(
                status="ANSWERABLE",
                selected_chunk_ids=[str(UUID(int=1))],
                unsupported_aspects=[],
            ),
            _draft("The Family plan allows ten dependents."),
            _draft("The Family plan allows up to 3 dependents."),
            VerificationDecision(supported=True, issues=[]),
        ]
    )

    result = _run(monkeypatch, provider)

    assert result.status == "ANSWERABLE"
    assert result.verification_status == "VERIFIED"
    assert result.regenerated is True


def test_multi_hop_answer_requires_the_mapping_citation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repaired_draft = AnswerDraft(
        answer="The Gold tier maps to the Family plan, which allows up to 3 dependents.",
        claims=[
            DraftClaim(
                text="The Gold tier maps to the Family plan, which allows up to 3 dependents.",
                evidence=[
                    EvidenceReference(
                        chunk_id=str(UUID(int=2)),
                        quote="The Gold tier maps to the Family plan.",
                    ),
                    EvidenceReference(
                        chunk_id=str(UUID(int=1)),
                        quote="The Family plan allows up to 3 dependents.",
                    ),
                ],
            )
        ],
    )
    provider = StubLLMProvider(
        [
            AnswerabilityDecision(
                status="ANSWERABLE",
                selected_chunk_ids=[str(UUID(int=1))],
                unsupported_aspects=[],
            ),
            _draft("The Family plan allows up to 3 dependents."),
            repaired_draft,
            VerificationDecision(supported=True, issues=[]),
        ]
    )
    monkeypatch.setattr(answering, "retrieve_knowledge", lambda *_: _multi_hop_retrieval())

    result = answer_knowledge(
        object(),
        object(),
        provider,
        Settings(_env_file=None),
        PROMPTS,
        "Tenho Gold. Quantos dependentes posso cadastrar?",
    )

    assert result.regenerated is True
    assert {citation.document_key for citation in result.citations} == {
        "employer-plans",
        "family-members",
    }


def test_normalized_exact_citation_is_accepted_without_regeneration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = StubLLMProvider(
        [
            AnswerabilityDecision(
                status="ANSWERABLE",
                selected_chunk_ids=[str(UUID(int=1))],
                unsupported_aspects=[],
            ),
            _draft("The Family plan allows up to  3 dependents."),
            VerificationDecision(supported=True, issues=[]),
        ]
    )

    result = _run(monkeypatch, provider)

    assert result.verification_status == "VERIFIED"
    assert result.regenerated is False


def test_second_grounding_failure_returns_safe_limitation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = StubLLMProvider(
        [
            AnswerabilityDecision(
                status="ANSWERABLE",
                selected_chunk_ids=[str(UUID(int=1))],
                unsupported_aspects=[],
            ),
            _draft("The Family plan allows ten dependents."),
            _draft("The Family plan allows eleven dependents."),
        ]
    )

    result = _run(monkeypatch, provider)

    assert result.status == "NOT_ANSWERABLE"
    assert result.answer == LIMITATION_ANSWER
    assert result.citations == ()
    assert result.verification_status == "FAILED_CLOSED"
    assert result.regenerated is True


def test_gate_cannot_select_unretrieved_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = StubLLMProvider(
        [
            AnswerabilityDecision(
                status="ANSWERABLE",
                selected_chunk_ids=[str(UUID(int=2))],
                unsupported_aspects=[],
            )
        ]
    )

    with pytest.raises(AnsweringError, match="unavailable evidence"):
        _run(monkeypatch, provider)


def test_ambiguous_answer_is_one_clarification_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = StubLLMProvider(
        [
            AnswerabilityDecision(
                status="AMBIGUOUS",
                selected_chunk_ids=[],
                unsupported_aspects=[],
                clarification_question="What is your plan or employer tier?",
            )
        ]
    )

    result = _run(monkeypatch, provider)

    assert result.status == "AMBIGUOUS"
    assert result.answer == "What is your plan or employer tier?"
    assert result.citations == ()
