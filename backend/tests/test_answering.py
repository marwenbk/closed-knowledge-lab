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
)
from app.config import Settings
from app.retrieval import RetrievalMatch, RetrievalResult
from pydantic import BaseModel


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
        document_title="Membros da família e dependentes",
        source_path="knowledge_base/05-family-members.md",
        section="Limites de dependentes",
        section_path=("Membros da família e dependentes", "Limites de dependentes"),
        ordinal=1,
        content="O plano Família permite o cadastro de **até 3 dependentes**.",
        rrf_score=1.0,
        signals={},
    )


def _retrieval() -> RetrievalResult:
    return RetrievalResult(
        query="Quantos dependentes o plano Família permite?",
        dataset_id="topmed-demo",
        dataset_version="2.0.0",
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
        content="O nível Gold corresponde ao plano Família.",
        rrf_score=1.0,
        signals={},
    )
    return RetrievalResult(
        query="Tenho Gold. Quantos dependentes posso cadastrar?",
        dataset_id="topmed-demo",
        dataset_version="2.0.0",
        embedding_model="test/embedding",
        embedding_version="a" * 40,
        matches=(mapping, _match()),
        trigram_fallback_used=False,
        second_hop_query="plano Família limite de dependentes",
        duration_ms=1.0,
    )


def _draft(quote: str) -> AnswerDraft:
    return AnswerDraft(
        answer="O plano Família permite até 3 dependentes.",
        claims=[
            DraftClaim(
                text="O plano Família permite até 3 dependentes.",
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
        "Quantos dependentes o plano Família permite?",
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
            _draft("O plano Família permite o cadastro de até 3 dependentes."),
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
            _draft("O plano Família permite dez dependentes."),
            _draft("O plano Família permite o cadastro de até 3 dependentes."),
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
        answer="O nível Gold segue o plano Família, que permite até 3 dependentes.",
        claims=[
            DraftClaim(
                text="O nível Gold segue o plano Família, que permite até 3 dependentes.",
                evidence=[
                    EvidenceReference(
                        chunk_id=str(UUID(int=2)),
                        quote="O nível Gold corresponde ao plano Família.",
                    ),
                    EvidenceReference(
                        chunk_id=str(UUID(int=1)),
                        quote="O plano Família permite o cadastro de até 3 dependentes.",
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
            _draft("O plano Família permite o cadastro de até 3 dependentes."),
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
            _draft("O plano Família permite o cadastro de até  3 dependentes."),
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
            _draft("O plano Família permite dez dependentes."),
            _draft("O plano Família permite onze dependentes."),
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
                clarification_question="Qual é o seu plano ou nível empresarial?",
            )
        ]
    )

    result = _run(monkeypatch, provider)

    assert result.status == "AMBIGUOUS"
    assert result.answer == "Qual é o seu plano ou nível empresarial?"
    assert result.citations == ()
