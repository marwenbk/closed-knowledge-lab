from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, desc, func, literal, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.embeddings import EmbeddingError, EmbeddingProvider, validate_embedding_vector
from app.kb import normalize_content
from app.models import Chunk, Document, KnowledgeBaseVersion


class RetrievalError(RuntimeError):
    pass


LEXICAL_TERM_PATTERN = re.compile(r"\b[\wÀ-ÿ]+(?:-[\wÀ-ÿ]+)*\b", re.UNICODE)
MAX_BROAD_LEXICAL_TERMS = 32
CHANNEL_WEIGHTS = {"lexical_broad": 0.5, "employer_mapping": 2.0}


@dataclass(frozen=True)
class Candidate:
    chunk_id: UUID
    stable_chunk_key: str
    document_key: str
    document_title: str
    source_path: str
    section: str
    section_path: tuple[str, ...]
    ordinal: int
    content: str
    score: float


@dataclass(frozen=True)
class RetrievalMatch:
    chunk_id: UUID
    stable_chunk_key: str
    document_key: str
    document_title: str
    source_path: str
    section: str
    section_path: tuple[str, ...]
    ordinal: int
    content: str
    rrf_score: float
    signals: dict[str, dict[str, float | int]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": str(self.chunk_id),
            "stable_chunk_key": self.stable_chunk_key,
            "document_key": self.document_key,
            "document_title": self.document_title,
            "source_path": self.source_path,
            "section": self.section,
            "section_path": list(self.section_path),
            "ordinal": self.ordinal,
            "content": self.content,
            "rrf_score": self.rrf_score,
            "signals": self.signals,
        }


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    dataset_id: str
    dataset_version: str
    embedding_model: str
    embedding_version: str
    matches: tuple[RetrievalMatch, ...]
    trigram_fallback_used: bool
    second_hop_query: str | None
    duration_ms: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "embedding_model": self.embedding_model,
            "embedding_version": self.embedding_version,
            "result_count": len(self.matches),
            "trigram_fallback_used": self.trigram_fallback_used,
            "second_hop": {
                "used": self.second_hop_query is not None,
                "query": self.second_hop_query,
            },
            "duration_ms": self.duration_ms,
            "matches": [match.as_dict() for match in self.matches],
        }


def _candidate(row: Any, score: float) -> Candidate:
    return Candidate(
        chunk_id=row.id,
        stable_chunk_key=row.stable_chunk_key,
        document_key=row.document_key,
        document_title=row.document_title,
        source_path=row.source_path,
        section=row.section,
        section_path=tuple(row.section_path),
        ordinal=row.ordinal,
        content=row.content,
        score=float(score),
    )


def _base_columns() -> tuple[Any, ...]:
    return (
        Chunk.id,
        Chunk.stable_chunk_key,
        Document.document_key.label("document_key"),
        Document.title.label("document_title"),
        Document.source_path,
        Chunk.section,
        Chunk.section_path,
        Chunk.ordinal,
        Chunk.content,
    )


def _semantic_candidates(
    session: Session,
    version_id: UUID,
    query_vector: list[float],
    provider: EmbeddingProvider,
    settings: Settings,
) -> list[Candidate]:
    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
    statement = (
        select(*_base_columns(), distance)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.kb_version_id == version_id,
            Chunk.embedding.is_not(None),
            Chunk.embedding_model == provider.model_id,
            Chunk.embedding_version == provider.model_version,
            Chunk.embedding_dimensions == provider.dimensions,
            Chunk.embedding_content_checksum == Chunk.content_checksum,
        )
        .order_by(distance, Chunk.stable_chunk_key)
        .limit(settings.vector_top_k)
    )
    return [_candidate(row, 1.0 - float(row.distance)) for row in session.execute(statement)]


def _lexical_candidates_for_tsquery(
    session: Session,
    version_id: UUID,
    tsquery: Any,
    limit: int,
    *,
    excluded_chunk_ids: set[UUID] | None = None,
) -> list[Candidate]:
    rank = func.ts_rank_cd(Chunk.search_vector, tsquery, 32).label("rank")
    predicates = [
        Chunk.kb_version_id == version_id,
        Chunk.search_vector.bool_op("@@")(tsquery),
    ]
    if excluded_chunk_ids:
        predicates.append(Chunk.id.not_in(excluded_chunk_ids))
    statement = (
        select(*_base_columns(), rank)
        .join(Document, Document.id == Chunk.document_id)
        .where(*predicates)
        .order_by(desc(rank), Chunk.stable_chunk_key)
        .limit(limit)
    )
    return [_candidate(row, row.rank) for row in session.execute(statement)]


def _broad_websearch_query(query: str) -> str | None:
    terms: list[str] = []
    seen: set[str] = set()
    for term in LEXICAL_TERM_PATTERN.findall(normalize_content(query)):
        if len(term) < 2 or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) == MAX_BROAD_LEXICAL_TERMS:
            break
    if not terms:
        return None
    # Terms come from the conservative pattern above. Quoting each one prevents
    # user input from becoming a websearch operator while retaining hyphenated IDs.
    return " OR ".join(f'"{term}"' for term in terms)


def _lexical_channels(
    session: Session,
    version_id: UUID,
    query: str,
    settings: Settings,
) -> tuple[list[Candidate], list[Candidate]]:
    strict = _lexical_candidates_for_tsquery(
        session,
        version_id,
        func.websearch_to_tsquery("portuguese", query),
        settings.lexical_top_k,
    )
    remaining = settings.lexical_top_k - len(strict)
    broad_query = _broad_websearch_query(query)
    if remaining <= 0 or broad_query is None:
        return strict, []
    broad = _lexical_candidates_for_tsquery(
        session,
        version_id,
        func.websearch_to_tsquery("portuguese", broad_query),
        remaining,
        excluded_chunk_ids={candidate.chunk_id for candidate in strict},
    )
    return strict, broad


def _trigram_candidates(
    session: Session,
    version_id: UUID,
    query: str,
    settings: Settings,
) -> list[Candidate]:
    normalized_query = normalize_content(query)
    session.scalar(
        select(
            func.set_config(
                "pg_trgm.word_similarity_threshold",
                format(settings.trigram_min_similarity, ".17g"),
                True,
            )
        )
    )
    similarity = func.word_similarity(normalized_query, Chunk.content_normalized).label(
        "similarity"
    )
    indexable_match = literal(normalized_query).bool_op("<%")(Chunk.content_normalized)
    statement = (
        select(*_base_columns(), similarity)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.kb_version_id == version_id,
            indexable_match,
        )
        .order_by(desc(similarity), Chunk.stable_chunk_key)
        .limit(settings.trigram_top_k)
    )
    return [_candidate(row, row.similarity) for row in session.execute(statement)]


def _run_channels(
    session: Session,
    version_id: UUID,
    query: str,
    query_vector: list[float],
    provider: EmbeddingProvider,
    settings: Settings,
    *,
    method_prefix: str = "",
) -> tuple[dict[str, list[Candidate]], bool]:
    semantic = _semantic_candidates(session, version_id, query_vector, provider, settings)
    lexical_strict, lexical_broad = _lexical_channels(session, version_id, query, settings)
    best_vector = semantic[0].score if semantic else -1.0
    use_trigram = (
        settings.trigram_fallback_enabled
        and not lexical_strict
        and best_vector < settings.min_vector_similarity
    )
    channels = {
        f"{method_prefix}vector": semantic,
        f"{method_prefix}lexical_strict": lexical_strict,
        f"{method_prefix}lexical_broad": lexical_broad,
    }
    if not method_prefix:
        mapping = _employer_mapping_candidates(session, version_id, query)
        if mapping:
            channels["employer_mapping"] = mapping
    if use_trigram:
        channels[f"{method_prefix}trigram"] = _trigram_candidates(
            session, version_id, query, settings
        )
    return channels, use_trigram


def _fuse(
    channels: dict[str, list[Candidate]], rrf_k: int, limit: int | None = None
) -> list[RetrievalMatch]:
    accumulated: dict[UUID, dict[str, Any]] = {}
    for method, candidates in channels.items():
        base_method = method.removeprefix("second_hop_")
        channel_weight = CHANNEL_WEIGHTS.get(base_method, 1.0)
        for rank, candidate in enumerate(candidates, start=1):
            state = accumulated.setdefault(
                candidate.chunk_id,
                {"candidate": candidate, "rrf_score": 0.0, "signals": {}},
            )
            contribution = channel_weight / (rrf_k + rank)
            state["rrf_score"] += contribution
            state["signals"][method] = {"rank": rank, "score": candidate.score}

    ordered = sorted(
        accumulated.values(),
        key=lambda state: (
            -float(state["rrf_score"]),
            state["candidate"].stable_chunk_key,
        ),
    )
    if limit is not None:
        ordered = ordered[:limit]
    return [
        RetrievalMatch(
            chunk_id=state["candidate"].chunk_id,
            stable_chunk_key=state["candidate"].stable_chunk_key,
            document_key=state["candidate"].document_key,
            document_title=state["candidate"].document_title,
            source_path=state["candidate"].source_path,
            section=state["candidate"].section,
            section_path=state["candidate"].section_path,
            ordinal=state["candidate"].ordinal,
            content=state["candidate"].content,
            rrf_score=state["rrf_score"],
            signals=state["signals"],
        )
        for state in ordered
    ]


EMPLOYER_TIER_PATTERN = re.compile(r"\b(silver|gold|platinum)\b", re.IGNORECASE)
MAPPING_PATTERN = re.compile(
    r"\b(silver|gold|platinum)\b\s*(?:\*\*)?\s*"
    r"(?:→|->|corresponde(?:\s+ao)?(?:\s+plano)?|usa\s+as\s+regras\s+do)\s*"
    r"(?:\*\*)?\s*(essencial|família|premium)\b",
    re.IGNORECASE,
)
SECOND_HOP_TOPICS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"dependen", re.IGNORECASE), "limite de dependentes"),
    (
        re.compile(r"quant(?:as|idade)?\s+(?:de\s+)?consult|consultas?.*mens", re.IGNORECASE),
        "limite mensal de consultas",
    ),
    (
        re.compile(
            r"especial|cobertura|dermat|psicolog|pediatr|nutri|cardio|gineco",
            re.IGNORECASE,
        ),
        "especialidades incluídas",
    ),
    (re.compile(r"preç|valor|custa|custo|mensalidade", re.IGNORECASE), "preço mensal"),
    (re.compile(r"horár|disponib|quando|sábado|domingo", re.IGNORECASE), "horários"),
)


def _mentioned_employer_tier(query: str) -> str | None:
    match = EMPLOYER_TIER_PATTERN.search(normalize_content(query))
    return normalize_content(match.group(1)) if match is not None else None


def _employer_mapping_candidates(
    session: Session,
    version_id: UUID,
    query: str,
) -> list[Candidate]:
    mentioned_tier = _mentioned_employer_tier(query)
    if mentioned_tier is None:
        return []
    statement = (
        select(*_base_columns())
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.kb_version_id == version_id,
            Document.document_key == "employer-plans",
            Chunk.content_normalized.contains(mentioned_tier),
        )
        .order_by(Chunk.stable_chunk_key)
    )
    for row in session.execute(statement):
        if any(
            normalize_content(mapping.group(1)) == mentioned_tier
            for mapping in MAPPING_PATTERN.finditer(row.content)
        ):
            return [_candidate(row, 1.0)]
    return []


def _second_hop_query(query: str, evidence: list[RetrievalMatch]) -> str | None:
    mentioned_tier = _mentioned_employer_tier(query)
    if mentioned_tier is None:
        return None
    topics = [description for pattern, description in SECOND_HOP_TOPICS if pattern.search(query)]
    if not topics:
        return None
    for match in evidence:
        for mapping in MAPPING_PATTERN.finditer(match.content):
            if normalize_content(mapping.group(1)) == mentioned_tier:
                consumer_plan = mapping.group(2)
                return f"{' e '.join(topics)} do plano {consumer_plan}"
    return None


def _validated_query_vector(
    provider: EmbeddingProvider,
    query: str,
    *,
    label: str,
) -> list[float]:
    vectors = provider.embed_queries([query])
    if len(vectors) != 1:
        raise RetrievalError(f"Embedding provider returned an invalid {label} vector")
    try:
        validate_embedding_vector(vectors[0], provider.dimensions)
    except (EmbeddingError, TypeError, ValueError) as exc:
        raise RetrievalError(f"Embedding provider returned an invalid {label} vector") from exc
    return list(vectors[0])


def retrieve_knowledge(
    engine: Engine,
    provider: EmbeddingProvider,
    settings: Settings,
    query: str,
) -> RetrievalResult:
    cleaned_query = query.strip()
    if not cleaned_query:
        raise RetrievalError("Retrieval query must not be empty")
    started_at = time.perf_counter()
    query_vector = _validated_query_vector(provider, cleaned_query, label="query")

    with Session(engine) as session:
        version = session.scalar(
            select(KnowledgeBaseVersion).where(
                KnowledgeBaseVersion.status == "ACTIVE",
                KnowledgeBaseVersion.dataset_id == settings.expected_dataset_id,
            )
        )
        if version is None:
            raise RetrievalError(
                "The expected active knowledge-base dataset is not loaded: "
                f"{settings.expected_dataset_id}"
            )
        total_chunks = session.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.kb_version_id == version.id)
        )
        current_embeddings = session.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(
                Chunk.kb_version_id == version.id,
                Chunk.embedding.is_not(None),
                Chunk.embedding_model == provider.model_id,
                Chunk.embedding_version == provider.model_version,
                Chunk.embedding_dimensions == provider.dimensions,
                Chunk.embedding_content_checksum == Chunk.content_checksum,
            )
        )
        if not total_chunks or current_embeddings != total_chunks:
            raise RetrievalError("The active knowledge base is not fully embedded")

        channels, trigram_used = _run_channels(
            session,
            version.id,
            cleaned_query,
            query_vector,
            provider,
            settings,
        )
        first_pass = _fuse(channels, settings.rrf_k, settings.final_context_k)
        second_query = (
            _second_hop_query(cleaned_query, first_pass) if settings.second_hop_enabled else None
        )
        if second_query is not None:
            second_vector = _validated_query_vector(
                provider,
                second_query,
                label="second-hop",
            )
            second_channels, second_trigram = _run_channels(
                session,
                version.id,
                second_query,
                second_vector,
                provider,
                settings,
                method_prefix="second_hop_",
            )
            channels.update(second_channels)
            trigram_used = trigram_used or second_trigram

        matches = tuple(_fuse(channels, settings.rrf_k, settings.final_context_k))
        return RetrievalResult(
            query=cleaned_query,
            dataset_id=version.dataset_id,
            dataset_version=version.dataset_version,
            embedding_model=provider.model_id,
            embedding_version=provider.model_version,
            matches=matches,
            trigram_fallback_used=trigram_used,
            second_hop_query=second_query,
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
