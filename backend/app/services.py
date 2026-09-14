from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Chunk, Document, KnowledgeBaseVersion
from app.tuning import TuningError, runtime_status

REQUIRED_EXTENSIONS = {"vector", "pg_trgm"}
REQUIRED_LEXICAL_INDEXES = {"ix_chunks_search_vector", "ix_chunks_content_trgm"}
MIGRATION_HEAD = "0010_english_runtime"
REQUIRED_EVENT_TABLES = {
    "admin_sessions",
    "admin_users",
    "conversation_events",
    "conversations",
    "handoff_events",
    "feedback",
    "messages",
    "message_reviews",
    "rag_runs",
    "widget_sessions",
}
logger = logging.getLogger("topmed.services")


class KnowledgeBaseUnavailable(RuntimeError):
    pass


def kb_status(engine: Engine, *, dataset_id: str) -> dict[str, Any]:
    with Session(engine) as session:
        version = session.scalar(
            select(KnowledgeBaseVersion).where(
                KnowledgeBaseVersion.status == "ACTIVE",
                KnowledgeBaseVersion.dataset_id == dataset_id,
            )
        )
        if version is None:
            raise KnowledgeBaseUnavailable(
                f"No active knowledge-base version is loaded for dataset {dataset_id!r}"
            )
        document_count = session.scalar(
            select(func.count()).select_from(Document).where(Document.kb_version_id == version.id)
        )
        chunk_count = session.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.kb_version_id == version.id)
        )
        embedding_rows = session.execute(
            select(
                Chunk.embedding_model,
                Chunk.embedding_version,
                Chunk.embedding_dimensions,
                func.count().label("chunk_count"),
            )
            .where(
                Chunk.kb_version_id == version.id,
                Chunk.embedding.is_not(None),
                Chunk.embedding_content_checksum == Chunk.content_checksum,
            )
            .group_by(
                Chunk.embedding_model,
                Chunk.embedding_version,
                Chunk.embedding_dimensions,
            )
        ).all()
        embedded_count = sum(row.chunk_count for row in embedding_rows)
        token_range = session.execute(
            select(func.min(Chunk.token_count), func.max(Chunk.token_count)).where(
                Chunk.kb_version_id == version.id
            )
        ).one()
        return {
            "dataset_id": version.dataset_id,
            "dataset_version": version.dataset_version,
            "generator_version": version.generator_version,
            "language": version.language,
            "status": version.status,
            "manifest_checksum": version.manifest_checksum,
            "document_count": int(document_count or 0),
            "chunk_count": int(chunk_count or 0),
            "embedded_chunk_count": int(embedded_count or 0),
            "embedding": {
                "status": (
                    "ready"
                    if chunk_count and embedded_count == chunk_count and len(embedding_rows) == 1
                    else "pending"
                ),
                "model_id": embedding_rows[0].embedding_model if len(embedding_rows) == 1 else None,
                "model_version": (
                    embedding_rows[0].embedding_version if len(embedding_rows) == 1 else None
                ),
                "dimensions": (
                    embedding_rows[0].embedding_dimensions if len(embedding_rows) == 1 else None
                ),
            },
            "chunk_token_range": {
                "minimum": int(token_range[0] or 0),
                "maximum": int(token_range[1] or 0),
            },
            "activated_at": version.activated_at.isoformat() if version.activated_at else None,
        }


def readiness(
    engine: Engine,
    *,
    expected_dataset_id: str,
    expected_dataset_version: str,
    expected_embedding_model: str,
    expected_embedding_version: str,
    expected_embedding_dimensions: int,
    embedding_runtime_ready: bool,
    llm_runtime_ready: bool,
    expected_chat_model: str,
    chat_model_version: str | None,
) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "database": {"status": "not_ready"},
        "migrations": {"status": "not_ready", "expected": MIGRATION_HEAD},
        "extensions": {"status": "not_ready", "required": sorted(REQUIRED_EXTENSIONS)},
        "knowledge_base": {"status": "not_ready"},
        "lexical_indexes": {
            "status": "not_ready",
            "required": sorted(REQUIRED_LEXICAL_INDEXES),
        },
        "semantic_index": {
            "status": "pending",
            "strategy": "exact",
            "ann_index": False,
            "ann_required": False,
        },
        "embedding_runtime": {"status": "ready" if embedding_runtime_ready else "not_ready"},
        "event_store": {
            "status": "not_ready",
            "required": sorted(REQUIRED_EVENT_TABLES),
        },
        "runtime_configuration": {"status": "not_ready"},
    }
    llm_matches = chat_model_version == expected_chat_model
    checks["llm_runtime"] = {
        "status": "ready" if llm_runtime_ready and llm_matches else "not_ready",
        "model": expected_chat_model,
        "version": chat_model_version,
    }
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            checks["database"] = {"status": "ready"}

            migration_table = connection.scalar(
                text("SELECT to_regclass('public.alembic_version')")
            )
            migration_version = None
            if migration_table:
                migration_version = connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
            checks["migrations"] = {
                "status": "ready" if migration_version == MIGRATION_HEAD else "not_ready",
                "current": migration_version,
                "expected": MIGRATION_HEAD,
            }

            installed_extensions = set(
                connection.execute(
                    text("SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pg_trgm')")
                ).scalars()
            )
            checks["extensions"] = {
                "status": (
                    "ready" if REQUIRED_EXTENSIONS.issubset(installed_extensions) else "not_ready"
                ),
                "required": sorted(REQUIRED_EXTENSIONS),
                "installed": sorted(installed_extensions),
            }

            indexes = set(
                connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = current_schema() AND tablename = 'chunks'"
                    )
                ).scalars()
            )
            checks["lexical_indexes"] = {
                "status": ("ready" if REQUIRED_LEXICAL_INDEXES.issubset(indexes) else "not_ready"),
                "required": sorted(REQUIRED_LEXICAL_INDEXES),
                "installed": sorted(REQUIRED_LEXICAL_INDEXES.intersection(indexes)),
            }
            event_tables = set(
                connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
                ).scalars()
            ).intersection(REQUIRED_EVENT_TABLES)
            checks["event_store"] = {
                "status": (
                    "ready" if REQUIRED_EVENT_TABLES.issubset(event_tables) else "not_ready"
                ),
                "required": sorted(REQUIRED_EVENT_TABLES),
                "installed": sorted(event_tables),
            }
        active = kb_status(engine, dataset_id=expected_dataset_id)
        knowledge_ready = (
            active["document_count"] > 0
            and active["chunk_count"] > 0
            and active["dataset_id"] == expected_dataset_id
            and active["dataset_version"] == expected_dataset_version
        )
        checks["knowledge_base"] = {
            "status": "ready" if knowledge_ready else "not_ready",
            "dataset_id": active["dataset_id"],
            "dataset_version": active["dataset_version"],
            "expected_dataset_id": expected_dataset_id,
            "expected_dataset_version": expected_dataset_version,
            "document_count": active["document_count"],
            "chunk_count": active["chunk_count"],
        }
        embedded = active["embedded_chunk_count"]
        total = active["chunk_count"]
        embedding = active["embedding"]
        metadata_matches = (
            embedding["model_id"] == expected_embedding_model
            and embedding["model_version"] == expected_embedding_version
            and embedding["dimensions"] == expected_embedding_dimensions
        )
        semantic_ready = (
            total > 0 and embedded == total and embedding["status"] == "ready" and metadata_matches
        )
        checks["semantic_index"] = {
            "status": "ready" if semantic_ready else "pending",
            "embedded_chunks": embedded,
            "total_chunks": total,
            "strategy": "exact",
            "ann_index": False,
            "ann_required": False,
            "model_id": embedding["model_id"],
            "model_version": embedding["model_version"],
            "dimensions": embedding["dimensions"],
            "expected_model_id": expected_embedding_model,
            "expected_model_version": expected_embedding_version,
            "expected_dimensions": expected_embedding_dimensions,
            "metadata_matches": metadata_matches,
        }
        checks["runtime_configuration"] = runtime_status(engine)
    except KnowledgeBaseUnavailable as exc:
        checks["knowledge_base"]["detail"] = str(exc)
    except TuningError as exc:
        checks["runtime_configuration"] = {"status": "not_ready", "detail": str(exc)}
    except SQLAlchemyError as exc:
        logger.warning("readiness database check failed", exc_info=exc)
        checks["database"] = {
            "status": "not_ready",
            "detail": "Database connectivity or metadata check failed",
        }

    required_checks = (
        "database",
        "migrations",
        "extensions",
        "knowledge_base",
        "lexical_indexes",
        "semantic_index",
        "embedding_runtime",
        "llm_runtime",
        "event_store",
        "runtime_configuration",
    )
    ready = all(checks[name]["status"] == "ready" for name in required_checks)
    return {"status": "ready" if ready else "not_ready", "checks": checks}
