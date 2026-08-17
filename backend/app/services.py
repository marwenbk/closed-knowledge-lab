from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Chunk, Document, KnowledgeBaseVersion

REQUIRED_EXTENSIONS = {"vector", "pg_trgm"}
REQUIRED_LEXICAL_INDEXES = {"ix_chunks_search_vector", "ix_chunks_content_trgm"}
MIGRATION_HEAD = "0001_knowledge_foundation"


class KnowledgeBaseUnavailable(RuntimeError):
    pass


def kb_status(engine: Engine) -> dict[str, Any]:
    with Session(engine) as session:
        version = session.scalar(
            select(KnowledgeBaseVersion).where(KnowledgeBaseVersion.status == "ACTIVE")
        )
        if version is None:
            raise KnowledgeBaseUnavailable("No active knowledge-base version is loaded")
        document_count = session.scalar(
            select(func.count()).select_from(Document).where(Document.kb_version_id == version.id)
        )
        chunk_count = session.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.kb_version_id == version.id)
        )
        embedded_count = session.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.kb_version_id == version.id, Chunk.embedding.is_not(None))
        )
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
            "chunk_token_range": {
                "minimum": int(token_range[0] or 0),
                "maximum": int(token_range[1] or 0),
            },
            "activated_at": version.activated_at.isoformat() if version.activated_at else None,
        }


def readiness(
    engine: Engine,
    *,
    expected_dataset_id: str | None = None,
    expected_dataset_version: str | None = None,
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
        "semantic_index": {"status": "pending", "ann_index": False},
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
            ann_index = any(
                "hnsw" in index_name or "ivfflat" in index_name for index_name in indexes
            )
            checks["semantic_index"]["ann_index"] = ann_index

        active = kb_status(engine)
        dataset_matches = expected_dataset_id is None or active["dataset_id"] == expected_dataset_id
        version_matches = (
            expected_dataset_version is None
            or active["dataset_version"] == expected_dataset_version
        )
        knowledge_ready = (
            active["document_count"] > 0
            and active["chunk_count"] > 0
            and dataset_matches
            and version_matches
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
        semantic_status = "ready" if total > 0 and embedded == total else "pending"
        checks["semantic_index"] = {
            "status": semantic_status,
            "embedded_chunks": embedded,
            "total_chunks": total,
            "ann_index": checks["semantic_index"]["ann_index"],
        }
    except KnowledgeBaseUnavailable as exc:
        checks["knowledge_base"]["detail"] = str(exc)
    except SQLAlchemyError as exc:
        checks["database"]["detail"] = str(exc)

    required_checks = ("database", "migrations", "extensions", "knowledge_base", "lexical_indexes")
    ready = all(checks[name]["status"] == "ready" for name in required_checks)
    return {"status": "ready" if ready else "not_ready", "checks": checks}
