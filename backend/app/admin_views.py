from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, func, or_, select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, Settings
from app.conversations import ConversationError
from app.models import (
    Chunk,
    Conversation,
    Document,
    DocumentRevision,
    KnowledgeBaseVersion,
    Message,
    RagRun,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _latest_evaluation() -> dict[str, Any] | None:
    candidates = (
        PROJECT_ROOT / "evals/full.report.json",
        PROJECT_ROOT / "evals/retrieval.report.json",
    )
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return None
    path = max(existing, key=lambda item: item.stat().st_mtime)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return {
        "mode": payload.get("mode"),
        "passed": payload.get("passed"),
        "dataset_version": payload.get("dataset_version"),
        "evaluated_cases": payload.get("answering", {}).get("evaluated_cases")
        or payload.get("retrieval", {}).get("evaluated_cases"),
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
    }


def dashboard_snapshot(engine: Engine, settings: Settings) -> dict[str, Any]:
    now = _now()
    with Session(engine) as session:
        version = session.scalar(
            select(KnowledgeBaseVersion).where(
                KnowledgeBaseVersion.dataset_id == settings.expected_dataset_id,
                KnowledgeBaseVersion.status == "ACTIVE",
            )
        )
        if version is None:
            raise ConversationError(503, "KNOWLEDGE_BASE_NOT_READY", "Knowledge base not ready")
        document_count = (
            session.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.kb_version_id == version.id)
            )
            or 0
        )
        chunk_count = (
            session.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.kb_version_id == version.id)
            )
            or 0
        )
        conversation_counts: dict[str, int] = {
            state: count
            for state, count in session.execute(
                select(Conversation.state, func.count()).group_by(Conversation.state)
            )
        }
        answerability: dict[str, int] = {
            status: count
            for status, count in session.execute(
                select(RagRun.answerability_status, func.count())
                .where(RagRun.answerability_status.is_not(None))
                .group_by(RagRun.answerability_status)
            )
            if status is not None
        }
        completed_runs = (
            session.scalar(
                select(func.count()).select_from(RagRun).where(RagRun.status == "COMPLETED")
            )
            or 0
        )
        average_latency = session.scalar(
            select(func.avg(RagRun.latency_ms)).where(RagRun.status == "COMPLETED")
        )
        grounding_failures = (
            session.scalar(
                select(func.count())
                .select_from(RagRun)
                .where(RagRun.verification_status == "FAILED_CLOSED")
            )
            or 0
        )
        average_handoff_wait = session.scalar(
            select(
                func.avg(
                    func.extract(
                        "epoch", Conversation.claimed_at - Conversation.handoff_requested_at
                    )
                )
            ).where(
                Conversation.claimed_at.is_not(None),
                Conversation.handoff_requested_at.is_not(None),
            )
        )
        oldest_request = session.scalar(
            select(func.min(Conversation.handoff_requested_at)).where(
                Conversation.state == "HUMAN_REQUESTED"
            )
        )
        latest_run = session.scalar(select(RagRun).order_by(RagRun.created_at.desc()).limit(1))

    def rate(status: str) -> float:
        return (
            round((answerability.get(status, 0) / completed_runs) * 100, 2)
            if completed_runs
            else 0.0
        )

    return {
        "generated_at": now,
        "knowledge": {
            "dataset_id": version.dataset_id,
            "dataset_version": version.dataset_version,
            "status": version.status,
            "document_count": document_count,
            "chunk_count": chunk_count,
        },
        "runtime": {
            "model": latest_run.model_name if latest_run else settings.chat_model,
            "model_version": latest_run.model_version if latest_run else None,
            "prompt_version": latest_run.prompt_version if latest_run else settings.prompt_version,
            "settings_version": latest_run.settings_version
            if latest_run
            else settings.settings_version,
            "embedding_version": (
                latest_run.embedding_version if latest_run else settings.embedding_model_revision
            ),
        },
        "conversations": {
            "open": sum(count for state, count in conversation_counts.items() if state != "CLOSED"),
            "waiting": conversation_counts.get("HUMAN_REQUESTED", 0),
            "assigned": conversation_counts.get("HUMAN_ASSIGNED", 0),
            "human_active": conversation_counts.get("HUMAN_ACTIVE", 0),
            "oldest_waiting_seconds": (
                max(0, int((now - oldest_request).total_seconds())) if oldest_request else 0
            ),
        },
        "quality": {
            "answerability": {str(key): value for key, value in answerability.items() if key},
            "refusal_rate_percent": rate("NOT_ANSWERABLE"),
            "conflict_rate_percent": rate("CONFLICTING_EVIDENCE"),
            "grounding_failure_rate_percent": round(
                grounding_failures / completed_runs * 100 if completed_runs else 0,
                2,
            ),
            "average_ai_latency_ms": round(float(average_latency or 0), 2),
            "average_handoff_wait_seconds": round(float(average_handoff_wait or 0), 2),
        },
        "latest_evaluation": _latest_evaluation(),
    }


def list_knowledge_documents(
    engine: Engine,
    *,
    dataset_id: str,
    query: str | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    with Session(engine) as session:
        version = session.scalar(
            select(KnowledgeBaseVersion).where(
                KnowledgeBaseVersion.dataset_id == dataset_id,
                KnowledgeBaseVersion.status == "ACTIVE",
            )
        )
        if version is None:
            raise ConversationError(503, "KNOWLEDGE_BASE_NOT_READY", "Knowledge base not ready")
        conditions = [Document.kb_version_id == version.id]
        if query:
            pattern = f"%{query.strip()}%"
            conditions.append(
                or_(
                    Document.document_key.ilike(pattern),
                    Document.title.ilike(pattern),
                    Document.source_path.ilike(pattern),
                )
            )
        total = session.scalar(select(func.count()).select_from(Document).where(*conditions)) or 0
        rows = session.execute(
            select(Document, func.count(Chunk.id).label("chunk_count"))
            .outerjoin(Chunk, Chunk.document_id == Document.id)
            .where(*conditions)
            .group_by(Document.id)
            .order_by(Document.sort_order, Document.document_key)
            .offset(offset)
            .limit(limit)
        ).all()
        return {
            "items": [
                {
                    "id": document.id,
                    "document_key": document.document_key,
                    "title": document.title,
                    "source_path": document.source_path,
                    "checksum": document.checksum,
                    "status": document.status,
                    "chunk_count": chunk_count,
                    "sort_order": document.sort_order,
                }
                for document, chunk_count in rows
            ],
            "total": total,
            "offset": offset,
            "limit": limit,
            "dataset_version": version.dataset_version,
        }


def get_knowledge_document(
    engine: Engine,
    *,
    dataset_id: str,
    document_id: UUID,
) -> dict[str, Any]:
    with Session(engine) as session:
        row = session.execute(
            select(Document, KnowledgeBaseVersion)
            .join(KnowledgeBaseVersion, KnowledgeBaseVersion.id == Document.kb_version_id)
            .where(
                Document.id == document_id,
                KnowledgeBaseVersion.dataset_id == dataset_id,
                KnowledgeBaseVersion.status == "ACTIVE",
            )
        ).one_or_none()
        if row is None:
            raise ConversationError(404, "DOCUMENT_NOT_FOUND", "Knowledge document not found")
        document, version = row
        revision = session.scalar(
            select(DocumentRevision)
            .where(DocumentRevision.document_id == document.id)
            .order_by(DocumentRevision.revision_number.desc())
            .limit(1)
        )
        chunks = session.scalars(
            select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.ordinal)
        ).all()
        return {
            "id": document.id,
            "document_key": document.document_key,
            "title": document.title,
            "source_path": document.source_path,
            "checksum": document.checksum,
            "status": document.status,
            "dataset_version": version.dataset_version,
            "metadata": document.metadata_json,
            "revision": (
                {
                    "revision_number": revision.revision_number,
                    "content_checksum": revision.content_checksum,
                    "front_matter": revision.front_matter,
                }
                if revision
                else None
            ),
            "chunks": [
                {
                    "id": chunk.id,
                    "stable_chunk_key": chunk.stable_chunk_key,
                    "section": chunk.section,
                    "section_path": chunk.section_path,
                    "ordinal": chunk.ordinal,
                    "content": chunk.content,
                    "token_count": chunk.token_count,
                }
                for chunk in chunks
            ],
        }


def get_rag_run(engine: Engine, rag_run_id: UUID) -> dict[str, Any]:
    with Session(engine) as session:
        run = session.get(RagRun, rag_run_id)
        if run is None:
            raise ConversationError(404, "RAG_RUN_NOT_FOUND", "RAG run not found")
        version = session.get(KnowledgeBaseVersion, run.kb_version_id)
        user_message = session.get(Message, run.user_message_id)
        assistant_message = (
            session.get(Message, run.assistant_message_id) if run.assistant_message_id else None
        )
        return {
            "id": run.id,
            "conversation_id": run.conversation_id,
            "user_message_id": run.user_message_id,
            "assistant_message_id": run.assistant_message_id,
            "original_query": run.original_query,
            "conversation_context": run.conversation_context_json,
            "retrieval_query": run.retrieval_query,
            "status": run.status,
            "answerability_status": run.answerability_status,
            "verification_status": run.verification_status,
            "error_code": run.error_code,
            "model": {
                "provider": run.model_provider,
                "name": run.model_name,
                "version": run.model_version,
                "prompt_version": run.prompt_version,
                "settings_version": run.settings_version,
                "embedding_version": run.embedding_version,
            },
            "knowledge": {
                "dataset_id": version.dataset_id if version else None,
                "dataset_version": version.dataset_version if version else None,
            },
            "user_message": user_message.content if user_message else None,
            "assistant_message": assistant_message.content if assistant_message else None,
            "citations": assistant_message.citations_json if assistant_message else [],
            "trace": run.execution_trace_json,
            "regenerated": run.regenerated,
            "latency_ms": run.latency_ms,
            "created_at": run.created_at,
            "completed_at": run.completed_at,
        }
