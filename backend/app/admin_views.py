from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, Settings
from app.conversations import ConversationError
from app.models import (
    Chunk,
    Conversation,
    Document,
    EvaluationRun,
    KnowledgeBaseVersion,
    Message,
    RagRun,
)
from app.tuning import runtime_status


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
        latest_evaluation_row = session.execute(
            select(EvaluationRun, KnowledgeBaseVersion.dataset_version)
            .join(KnowledgeBaseVersion, KnowledgeBaseVersion.id == EvaluationRun.kb_version_id)
            .order_by(EvaluationRun.started_at.desc())
            .limit(1)
        ).one_or_none()

    def rate(status: str) -> float:
        return (
            round((answerability.get(status, 0) / completed_runs) * 100, 2)
            if completed_runs
            else 0.0
        )

    runtime = runtime_status(engine)
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
            "prompt_version": runtime["prompt_version"],
            "settings_version": runtime["settings_version"],
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
        "latest_evaluation": (
            {
                "mode": latest_evaluation_row[0].suite,
                "passed": latest_evaluation_row[0].status == "PASSED",
                "dataset_version": latest_evaluation_row[1],
                "evaluated_cases": latest_evaluation_row[0]
                .metrics_json.get("retrieval", {})
                .get("evaluated_cases"),
                "updated_at": (
                    latest_evaluation_row[0].completed_at or latest_evaluation_row[0].started_at
                ).isoformat(),
            }
            if latest_evaluation_row
            else _latest_evaluation()
        ),
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
