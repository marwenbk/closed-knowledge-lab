from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

import pytest
from app import retrieval
from app.config import Settings
from app.retrieval import (
    Candidate,
    RetrievalError,
    RetrievalMatch,
    _broad_websearch_query,
    _fuse,
    _mentioned_employer_tier,
    _second_hop_query,
    _topic_companion_document_keys,
    _validated_query_vector,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session


class StubEmbeddingProvider:
    model_id = "test/model"
    model_version = "a" * 40

    def __init__(self, vector: Sequence[Any]) -> None:
        self.vector = vector
        self.dimensions = len(vector)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("document embedding is not expected in this test")

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        assert len(texts) == 1
        return [list(self.vector)]


def _candidate(number: int, stable_key: str) -> Candidate:
    return Candidate(
        chunk_id=UUID(int=number),
        stable_chunk_key=stable_key,
        document_key=f"document-{number}",
        document_title=f"Document {number}",
        source_path=f"knowledge_base/{number:02d}.md",
        section="Section",
        section_path=("Document", "Section"),
        ordinal=1,
        content="Approved evidence.",
        score=1.0,
    )


def _mapping_match(content: str) -> RetrievalMatch:
    candidate = _candidate(10, "employer-plans__mapping__001")
    return RetrievalMatch(
        chunk_id=candidate.chunk_id,
        stable_chunk_key=candidate.stable_chunk_key,
        document_key="employer-plans",
        document_title="Planos patrocinados por empresas",
        source_path="knowledge_base/06-employer-plans.md",
        section="Correspondência dos níveis",
        section_path=("Planos patrocinados por empresas", "Correspondência dos níveis"),
        ordinal=1,
        content=content,
        rrf_score=1.0,
        signals={},
    )


def test_broad_websearch_terms_are_deduplicated_and_quoted() -> None:
    query = 'Gold GOLD " OR família TM-REF-014'

    assert _broad_websearch_query(query) == ('"gold" OR "or" OR "família" OR "tm-ref-014"')


def test_lexical_backfill_only_uses_remaining_slots_and_excludes_strict_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strict = [_candidate(1, "strict-one"), _candidate(2, "strict-two")]
    broad = [_candidate(3, "broad-fill")]
    calls: list[tuple[int, set[UUID]]] = []

    def candidates_for_query(
        _session: Session,
        _version_id: UUID,
        _tsquery: Any,
        limit: int,
        *,
        excluded_chunk_ids: set[UUID] | None = None,
    ) -> list[Candidate]:
        calls.append((limit, excluded_chunk_ids or set()))
        return strict if len(calls) == 1 else broad

    monkeypatch.setattr(retrieval, "_lexical_candidates_for_tsquery", candidates_for_query)

    strict_result, broad_result = retrieval._lexical_channels(
        cast(Session, object()),
        UUID(int=99),
        "Gold e dependentes",
        Settings(lexical_top_k=3),
    )

    assert strict_result == strict
    assert broad_result == broad
    assert calls == [
        (3, set()),
        (1, {strict[0].chunk_id, strict[1].chunk_id}),
    ]


def test_trigram_query_uses_transaction_local_threshold_and_indexable_operator() -> None:
    class RecordingSession:
        def __init__(self) -> None:
            self.scalar_statement: Any = None
            self.query_statement: Any = None

        def scalar(self, statement: Any) -> None:
            self.scalar_statement = statement

        def execute(self, statement: Any) -> list[Any]:
            self.query_statement = statement
            return []

    session = RecordingSession()

    assert (
        retrieval._trigram_candidates(
            cast(Session, session),
            UUID(int=99),
            "dependntes",
            Settings(trigram_min_similarity=0.42),
        )
        == []
    )

    threshold_sql = str(session.scalar_statement.compile(dialect=postgresql.dialect()))
    query_sql = str(session.query_statement.compile(dialect=postgresql.dialect()))
    assert "set_config" in threshold_sql
    assert (
        "pg_trgm.word_similarity_threshold"
        in session.scalar_statement.compile(dialect=postgresql.dialect()).params.values()
    )
    assert "word_similarity" in query_sql
    assert "<% chunks.content_normalized" in query_sql.replace("%%", "%")


def test_weighted_fusion_penalizes_broad_lexical_candidates() -> None:
    broad_only = _candidate(1, "broad-only")
    strict = _candidate(2, "strict")

    matches = _fuse(
        {
            "vector": [broad_only, strict],
            "lexical_strict": [strict],
            "lexical_broad": [broad_only],
        },
        rrf_k=60,
    )

    assert [match.stable_chunk_key for match in matches] == ["strict", "broad-only"]


def test_mapping_channel_is_prioritized_and_ties_are_deterministic() -> None:
    mapping = _candidate(1, "mapping")
    beta = _candidate(2, "beta")
    alpha = _candidate(3, "alpha")

    matches = _fuse(
        {
            "vector": [beta],
            "lexical_strict": [alpha],
            "employer_mapping": [mapping],
        },
        rrf_k=60,
    )

    assert [match.stable_chunk_key for match in matches] == ["mapping", "alpha", "beta"]


def test_platinum_psychology_mapping_produces_one_plan_focused_second_hop() -> None:
    evidence = [_mapping_match("- **Platinum** → **Premium**")]

    query = _second_hop_query(
        "Tenho Platinum. Quantas consultas de psicologia tenho e qual o horário?",
        evidence,
    )

    assert query == (
        "limite mensal de consultas e especialidades incluídas e horários do plano Premium"
    )
    assert _second_hop_query("Tenho Platinum, mas qual é o meu benefício?", evidence) is None


def test_employer_tier_detection_requires_a_complete_word() -> None:
    assert _mentioned_employer_tier("Tenho Gold pela empresa") == "gold"
    assert _mentioned_employer_tier("O produto Golden serve?") is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "Dermatologia do plano Família funciona sábado à noite?",
            {"consultation-hours", "specialties"},
        ),
        (
            "Se eu cancelar hoje, recebo automaticamente o que paguei?",
            {"cancellation", "refund-policy"},
        ),
        (
            "O benefício empresarial Gold dá direito a quantos dependentes?",
            {"employer-plans", "family-members"},
        ),
        ("Quanto custa o plano Essencial?", set()),
    ],
)
def test_topic_companions_are_bounded_to_explicit_cross_document_intents(
    query: str, expected: set[str]
) -> None:
    assert _topic_companion_document_keys(query) == expected


@pytest.mark.parametrize(
    "vector",
    [
        [0.0, 0.0],
        [math.nan, 1.0],
        [math.inf, 1.0],
        [1e200, 1e200],
        ["invalid", 1.0],
    ],
)
def test_query_vector_validation_fails_closed(vector: Sequence[Any]) -> None:
    provider = StubEmbeddingProvider(vector)

    with pytest.raises(RetrievalError, match="invalid query vector"):
        _validated_query_vector(provider, "question", label="query")


def test_query_vector_validation_accepts_finite_nonzero_values() -> None:
    provider = StubEmbeddingProvider([1, 0.5])

    assert _validated_query_vector(provider, "question", label="query") == [1.0, 0.5]
