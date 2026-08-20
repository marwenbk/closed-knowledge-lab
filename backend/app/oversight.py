from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.conversations import ConversationError, record_audit_event
from app.models import AdminUser, AuditEvent, Feedback, RagRun

FeedbackCategory = Literal[
    "CORRECT",
    "INCORRECT",
    "MISSING_KB_INFORMATION",
    "CONFLICTING_KB_INFORMATION",
    "RETRIEVAL_FAILURE",
    "GROUNDING_FAILURE",
    "ESCALATION_APPROPRIATE",
    "ESCALATION_UNNECESSARY",
]


def create_feedback(
    engine: Engine,
    *,
    rag_run_id: UUID,
    category: FeedbackCategory,
    note: str | None,
    actor_id: UUID,
    request_id: UUID,
) -> dict[str, Any]:
    with Session(engine) as session, session.begin():
        run = session.get(RagRun, rag_run_id)
        if run is None:
            raise ConversationError(404, "RAG_RUN_NOT_FOUND", "RAG run not found")
        feedback = Feedback(
            id=uuid4(),
            rag_run_id=run.id,
            category=category,
            note=note.strip() if note and note.strip() else None,
            created_by=actor_id,
        )
        session.add(feedback)
        session.flush()
        record_audit_event(
            session,
            "feedback.created",
            "feedback",
            feedback.id,
            actor_type="ADMIN",
            actor_id=str(actor_id),
            request_id=request_id,
            after={
                "rag_run_id": str(run.id),
                "conversation_id": str(run.conversation_id),
                "category": category,
            },
        )
        return {
            "id": feedback.id,
            "rag_run_id": feedback.rag_run_id,
            "conversation_id": run.conversation_id,
            "category": feedback.category,
            "note": feedback.note,
            "created_by": feedback.created_by,
            "created_by_name": None,
            "created_at": feedback.created_at,
        }


def list_feedback(
    engine: Engine,
    *,
    rag_run_id: UUID | None,
    category: FeedbackCategory | None,
    offset: int,
    limit: int,
) -> tuple[tuple[dict[str, Any], ...], int]:
    conditions = []
    if rag_run_id is not None:
        conditions.append(Feedback.rag_run_id == rag_run_id)
    if category is not None:
        conditions.append(Feedback.category == category)
    with Session(engine) as session:
        total = session.scalar(select(func.count()).select_from(Feedback).where(*conditions)) or 0
        rows = session.execute(
            select(Feedback, RagRun.conversation_id, AdminUser.display_name)
            .join(RagRun, RagRun.id == Feedback.rag_run_id)
            .join(AdminUser, AdminUser.id == Feedback.created_by)
            .where(*conditions)
            .order_by(Feedback.created_at.desc(), Feedback.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return (
            tuple(
                {
                    "id": feedback.id,
                    "rag_run_id": feedback.rag_run_id,
                    "conversation_id": conversation_id,
                    "category": feedback.category,
                    "note": feedback.note,
                    "created_by": feedback.created_by,
                    "created_by_name": display_name,
                    "created_at": feedback.created_at,
                }
                for feedback, conversation_id, display_name in rows
            ),
            int(total),
        )


def list_audit_events(
    engine: Engine,
    *,
    event_type: str | None,
    actor_type: str | None,
    resource_type: str | None,
    resource_id: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
    offset: int,
    limit: int,
) -> tuple[tuple[dict[str, Any], ...], int]:
    conditions = []
    if event_type:
        conditions.append(AuditEvent.event_type == event_type)
    if actor_type:
        conditions.append(AuditEvent.actor_type == actor_type)
    if resource_type:
        conditions.append(AuditEvent.resource_type == resource_type)
    if resource_id:
        conditions.append(AuditEvent.resource_id == resource_id)
    if created_from:
        conditions.append(AuditEvent.created_at >= created_from)
    if created_to:
        conditions.append(AuditEvent.created_at <= created_to)
    with Session(engine) as session:
        total = session.scalar(select(func.count()).select_from(AuditEvent).where(*conditions)) or 0
        events = session.scalars(
            select(AuditEvent)
            .where(*conditions)
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return (
            tuple(
                {
                    "id": event.id,
                    "event_type": event.event_type,
                    "actor_type": event.actor_type,
                    "actor_id": event.actor_id,
                    "resource_type": event.resource_type,
                    "resource_id": event.resource_id,
                    "request_id": event.request_id,
                    "before": event.before_json,
                    "after": event.after_json,
                    "metadata": event.metadata_json,
                    "created_at": event.created_at,
                }
                for event in events
            ),
            int(total),
        )
