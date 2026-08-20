from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.answering import GroundedAnswer, answer_knowledge_with_trace, contextualize_query
from app.config import Settings
from app.embeddings import EmbeddingProvider
from app.llm import LLMProvider
from app.models import (
    AuditEvent,
    Conversation,
    ConversationEvent,
    HandoffEvent,
    KnowledgeBaseVersion,
    Message,
    RagRun,
    WidgetSession,
)
from app.tuning import RuntimeSnapshot, TuningError, runtime_snapshot

ConversationState = Literal[
    "AI_ACTIVE",
    "AI_REVIEW_PENDING",
    "HUMAN_REQUESTED",
    "HUMAN_ASSIGNED",
    "HUMAN_ACTIVE",
    "RETURNED_TO_AI",
    "CLOSED",
]

VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "AI_ACTIVE": frozenset({"AI_REVIEW_PENDING", "HUMAN_REQUESTED", "CLOSED"}),
    "AI_REVIEW_PENDING": frozenset({"AI_ACTIVE", "HUMAN_REQUESTED", "CLOSED"}),
    "HUMAN_REQUESTED": frozenset({"HUMAN_ASSIGNED", "HUMAN_ACTIVE", "CLOSED"}),
    "HUMAN_ASSIGNED": frozenset({"HUMAN_ACTIVE", "RETURNED_TO_AI", "CLOSED"}),
    "HUMAN_ACTIVE": frozenset({"RETURNED_TO_AI", "CLOSED"}),
    "RETURNED_TO_AI": frozenset({"AI_ACTIVE", "HUMAN_REQUESTED", "CLOSED"}),
    "CLOSED": frozenset(),
}


class ConversationError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class WidgetPrincipal:
    session_id: UUID
    anonymous_subject: str
    origin: str
    expires_at: datetime


@dataclass(frozen=True)
class SessionResult:
    session_id: UUID
    token: str
    expires_at: datetime
    locale: str


@dataclass(frozen=True)
class PublicMessage:
    message_id: UUID
    sender_type: str
    content: str
    status: str
    citations: tuple[dict[str, Any], ...]
    rag_run_id: UUID | None
    created_at: datetime
    delivered_at: datetime | None


@dataclass(frozen=True)
class ConversationSnapshot:
    conversation_id: UUID
    state: str
    messages: tuple[PublicMessage, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SubmissionResult:
    conversation_id: UUID
    message_id: UUID
    rag_run_id: UUID
    answer: GroundedAnswer


@dataclass(frozen=True)
class QueuedSubmissionResult:
    conversation_id: UUID
    message_id: UUID
    state: str


@dataclass(frozen=True)
class ReviewPendingResult:
    conversation_id: UUID
    message_id: UUID
    rag_run_id: UUID
    state: str = "AI_REVIEW_PENDING"


MessageSubmissionResult = SubmissionResult | QueuedSubmissionResult | ReviewPendingResult


@dataclass(frozen=True)
class EventRecord:
    event_id: int
    conversation_id: UUID
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def normalize_origin(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        port_number = parsed.port
    except ValueError as exc:
        raise ConversationError(
            403, "ORIGIN_NOT_ALLOWED", "The widget origin is not allowed"
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ConversationError(403, "ORIGIN_NOT_ALLOWED", "The widget origin is not allowed")
    hostname = parsed.hostname.lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 80 if parsed.scheme == "http" else 443
    port = f":{port_number}" if port_number is not None and port_number != default_port else ""
    return f"{parsed.scheme}://{host}{port}"


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _session_token(settings: Settings, session: WidgetSession) -> str:
    payload = _encode(
        json.dumps(
            {
                "exp": int(session.expires_at.timestamp()),
                "origin": session.origin,
                "sid": str(session.id),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    signed = f"v1.{payload}"
    signature = hmac.new(
        settings.widget_token_secret.get_secret_value().encode(),
        signed.encode(),
        hashlib.sha256,
    ).digest()
    return f"{signed}.{_encode(signature)}"


def _token_claims(settings: Settings, token: str) -> tuple[UUID, str, datetime]:
    try:
        version, payload, supplied_signature = token.split(".")
        if version != "v1":
            raise ValueError
        signed = f"{version}.{payload}"
        expected_signature = hmac.new(
            settings.widget_token_secret.get_secret_value().encode(),
            signed.encode(),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_decode(supplied_signature), expected_signature):
            raise ValueError
        claims = json.loads(_decode(payload))
        session_id = UUID(claims["sid"])
        origin = normalize_origin(claims["origin"])
        expires_at = datetime.fromtimestamp(int(claims["exp"]), UTC)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ConversationError(
            401,
            "INVALID_WIDGET_SESSION",
            "The widget session is invalid or expired",
        ) from exc
    if expires_at <= _now():
        raise ConversationError(
            401,
            "INVALID_WIDGET_SESSION",
            "The widget session is invalid or expired",
        )
    return session_id, origin, expires_at


def create_widget_session(
    engine: Engine,
    settings: Settings,
    *,
    assistant_key: str,
    origin: str,
    anonymous_subject: str | None,
    locale: str,
) -> SessionResult:
    if not hmac.compare_digest(
        assistant_key.encode(), settings.widget_assistant_key.get_secret_value().encode()
    ):
        raise ConversationError(
            401,
            "INVALID_ASSISTANT_KEY",
            "The assistant key or origin is invalid",
        )
    normalized_origin = normalize_origin(origin)
    allowed_origins = {normalize_origin(value) for value in settings.allowed_widget_origins}
    if normalized_origin not in allowed_origins:
        raise ConversationError(
            401,
            "INVALID_ASSISTANT_KEY",
            "The assistant key or origin is invalid",
        )
    expires_at = (_now() + timedelta(seconds=settings.widget_session_ttl_seconds)).replace(
        microsecond=0
    )
    widget_session = WidgetSession(
        id=uuid4(),
        anonymous_subject=anonymous_subject or str(uuid4()),
        origin=normalized_origin,
        locale=locale,
        expires_at=expires_at,
    )
    session_id = widget_session.id
    with Session(engine) as session, session.begin():
        session.add(widget_session)
        session.flush()
        token = _session_token(settings, widget_session)
    return SessionResult(
        session_id=session_id,
        token=token,
        expires_at=expires_at,
        locale=locale,
    )


def authenticate_widget_session(
    engine: Engine,
    settings: Settings,
    *,
    token: str,
    origin: str,
) -> WidgetPrincipal:
    session_id, token_origin, token_expires_at = _token_claims(settings, token)
    request_origin = normalize_origin(origin)
    if not hmac.compare_digest(token_origin, request_origin):
        raise ConversationError(403, "SESSION_ORIGIN_MISMATCH", "Widget session origin mismatch")
    now = _now()
    with Session(engine) as session, session.begin():
        widget_session = session.get(WidgetSession, session_id)
        if (
            widget_session is None
            or widget_session.expires_at <= now
            or widget_session.origin != token_origin
            or widget_session.expires_at != token_expires_at
        ):
            raise ConversationError(
                401,
                "INVALID_WIDGET_SESSION",
                "The widget session is invalid or expired",
            )
        widget_session.last_seen_at = now
        return WidgetPrincipal(
            session_id=widget_session.id,
            anonymous_subject=widget_session.anonymous_subject,
            origin=widget_session.origin,
            expires_at=widget_session.expires_at,
        )


def record_conversation_event(
    session: Session,
    conversation_id: UUID,
    event_type: str,
    payload: dict[str, Any],
    *,
    actor_type: str,
    actor_id: str | None,
    visibility: str = "PUBLIC",
) -> None:
    session.add(
        ConversationEvent(
            conversation_id=conversation_id,
            event_type=event_type,
            visibility=visibility,
            payload_json=payload,
            actor_type=actor_type,
            actor_id=actor_id,
        )
    )


def record_audit_event(
    session: Session,
    event_type: str,
    resource_type: str,
    resource_id: UUID,
    *,
    actor_type: str,
    actor_id: str | None,
    request_id: UUID,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            id=uuid4(),
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=str(resource_id),
            request_id=request_id,
            before_json=before,
            after_json=after,
            metadata_json=metadata or {},
        )
    )


def owned_conversation(
    session: Session,
    principal: WidgetPrincipal,
    conversation_id: UUID,
    *,
    for_update: bool = False,
) -> Conversation:
    statement = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.widget_session_id == principal.session_id,
    )
    if for_update:
        statement = statement.with_for_update()
    conversation = session.scalar(statement)
    if conversation is None:
        raise ConversationError(404, "CONVERSATION_NOT_FOUND", "Conversation not found")
    return conversation


def create_or_restore_conversation(
    engine: Engine,
    principal: WidgetPrincipal,
    *,
    conversation_id: UUID | None,
    request_id: UUID,
) -> tuple[ConversationSnapshot, bool]:
    with Session(engine) as session, session.begin():
        if conversation_id is not None:
            conversation = owned_conversation(session, principal, conversation_id)
            return conversation_snapshot(session, conversation), False
        conversation = Conversation(
            id=uuid4(),
            widget_session_id=principal.session_id,
            state="AI_ACTIVE",
        )
        session.add(conversation)
        session.flush()
        record_conversation_event(
            session,
            conversation.id,
            "conversation.created",
            {"conversation_id": str(conversation.id), "state": conversation.state},
            actor_type="CUSTOMER",
            actor_id=principal.anonymous_subject,
        )
        record_audit_event(
            session,
            "conversation.created",
            "conversation",
            conversation.id,
            actor_type="CUSTOMER",
            actor_id=principal.anonymous_subject,
            request_id=request_id,
            after={"state": conversation.state},
        )
        return conversation_snapshot(session, conversation), True


def conversation_snapshot(session: Session, conversation: Conversation) -> ConversationSnapshot:
    rows = session.execute(
        select(Message, RagRun.id)
        .outerjoin(RagRun, RagRun.assistant_message_id == Message.id)
        .where(
            Message.conversation_id == conversation.id,
            Message.visibility == "PUBLIC",
        )
        .order_by(Message.created_at, Message.id)
    ).all()
    messages = tuple(
        PublicMessage(
            message_id=message.id,
            sender_type=message.sender_type,
            content=message.content,
            status=message.status,
            citations=tuple(message.citations_json),
            rag_run_id=rag_run_id,
            created_at=message.created_at,
            delivered_at=message.delivered_at,
        )
        for message, rag_run_id in rows
    )
    return ConversationSnapshot(
        conversation_id=conversation.id,
        state=conversation.state,
        messages=messages,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def get_conversation(
    engine: Engine,
    principal: WidgetPrincipal,
    conversation_id: UUID,
) -> ConversationSnapshot:
    with Session(engine) as session:
        return conversation_snapshot(
            session, owned_conversation(session, principal, conversation_id)
        )


def _completed_submission(
    session: Session,
    conversation_id: UUID,
    run: RagRun,
) -> SubmissionResult | ReviewPendingResult:
    if run.assistant_message_id is None:
        if run.status == "RUNNING":
            raise ConversationError(409, "MESSAGE_PROCESSING", "The message is still processing")
        raise ConversationError(503, "MESSAGE_FAILED", "The message could not be processed")
    assistant_message = session.get(Message, run.assistant_message_id)
    version = session.get(KnowledgeBaseVersion, run.kb_version_id)
    if assistant_message is None or version is None:
        raise ConversationError(503, "MESSAGE_FAILED", "The message could not be restored")
    if assistant_message.review_status in {"PENDING", "REGENERATING"}:
        return ReviewPendingResult(
            conversation_id,
            assistant_message.id,
            run.id,
        )
    answer = GroundedAnswer.model_validate(
        {
            "status": run.answerability_status,
            "answer": assistant_message.content,
            "citations": assistant_message.citations_json,
            "dataset_id": version.dataset_id,
            "dataset_version": version.dataset_version,
            "model": {
                "provider": run.model_provider,
                "name": run.model_name,
                "version": run.model_version,
                "prompt_version": run.prompt_version,
            },
            "verification_status": run.verification_status,
            "regenerated": run.regenerated,
            "duration_ms": run.latency_ms,
        }
    )
    return SubmissionResult(conversation_id, assistant_message.id, run.id, answer)


def _persist_customer_message(
    session: Session,
    conversation: Conversation,
    principal: WidgetPrincipal,
    *,
    client_message_id: UUID,
    content: str,
    request_id: UUID,
    created_at: datetime,
) -> Message:
    message = Message(
        id=uuid4(),
        conversation_id=conversation.id,
        client_message_id=client_message_id,
        sender_type="CUSTOMER",
        content=content,
        visibility="PUBLIC",
        status="PERSISTED",
        citations_json=[],
    )
    session.add(message)
    conversation.last_message_at = created_at
    conversation.updated_at = created_at
    record_conversation_event(
        session,
        conversation.id,
        "message.created",
        {
            "message_id": str(message.id),
            "sender": {"type": "CUSTOMER", "label": "Você"},
            "content": content,
            "citations": [],
            "created_at": created_at.isoformat(),
        },
        actor_type="CUSTOMER",
        actor_id=principal.anonymous_subject,
    )
    record_audit_event(
        session,
        "message.submitted",
        "message",
        message.id,
        actor_type="CUSTOMER",
        actor_id=principal.anonymous_subject,
        request_id=request_id,
        after={"conversation_id": str(conversation.id), "status": "PERSISTED"},
    )
    return message


def _start_submission(
    engine: Engine,
    principal: WidgetPrincipal,
    settings: Settings,
    *,
    conversation_id: UUID,
    client_message_id: UUID,
    content: str,
    request_id: UUID,
) -> tuple[UUID, UUID, tuple[str, ...], RuntimeSnapshot] | MessageSubmissionResult:
    now = _now()
    with Session(engine) as session, session.begin():
        conversation = owned_conversation(session, principal, conversation_id, for_update=True)
        existing_message = session.scalar(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.client_message_id == client_message_id,
            )
        )
        if existing_message is not None:
            if existing_message.content != content:
                raise ConversationError(
                    409,
                    "IDEMPOTENCY_KEY_REUSED",
                    "The client message ID was already used with different content",
                )
            existing_run = session.scalar(
                select(RagRun).where(RagRun.user_message_id == existing_message.id)
            )
            if existing_run is None:
                return QueuedSubmissionResult(
                    conversation.id,
                    existing_message.id,
                    conversation.state,
                )
            return _completed_submission(session, conversation.id, existing_run)
        if conversation.state == "CLOSED":
            raise ConversationError(409, "CONVERSATION_CLOSED", "The conversation is closed")
        if conversation.state in {"HUMAN_REQUESTED", "HUMAN_ASSIGNED", "HUMAN_ACTIVE"}:
            message = _persist_customer_message(
                session,
                conversation,
                principal,
                client_message_id=client_message_id,
                content=content,
                request_id=request_id,
                created_at=now,
            )
            return QueuedSubmissionResult(conversation.id, message.id, conversation.state)
        if conversation.state not in {"AI_ACTIVE", "RETURNED_TO_AI"}:
            raise ConversationError(409, "AI_NOT_AVAILABLE", "The assistant is not available")
        running = session.scalar(
            select(RagRun.id).where(
                RagRun.conversation_id == conversation.id,
                RagRun.status == "RUNNING",
            )
        )
        if running is not None:
            raise ConversationError(409, "MESSAGE_PROCESSING", "Another message is processing")
        version = session.scalar(
            select(KnowledgeBaseVersion).where(
                KnowledgeBaseVersion.dataset_id == settings.expected_dataset_id,
                KnowledgeBaseVersion.status == "ACTIVE",
            )
        )
        if version is None:
            raise ConversationError(
                503,
                "KNOWLEDGE_BASE_NOT_READY",
                "The knowledge base is not ready",
            )
        try:
            runtime = runtime_snapshot(session, settings)
        except TuningError as exc:
            raise ConversationError(exc.status_code, exc.code, str(exc)) from exc
        previous_state = conversation.state
        if previous_state == "RETURNED_TO_AI":
            conversation.state = "AI_ACTIVE"
            conversation.updated_at = now
            record_conversation_event(
                session,
                conversation.id,
                "conversation.ai_resumed",
                {"from_state": previous_state, "to_state": "AI_ACTIVE"},
                actor_type="CUSTOMER",
                actor_id=principal.anonymous_subject,
            )
            record_audit_event(
                session,
                "conversation.ai_resumed",
                "conversation",
                conversation.id,
                actor_type="CUSTOMER",
                actor_id=principal.anonymous_subject,
                request_id=request_id,
                before={"state": previous_state},
                after={"state": "AI_ACTIVE"},
            )
        previous_messages = tuple(
            reversed(
                session.scalars(
                    select(Message.content)
                    .where(
                        Message.conversation_id == conversation.id,
                        Message.sender_type == "CUSTOMER",
                        Message.visibility == "PUBLIC",
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                ).all()
            )
        )
        retrieval_query = contextualize_query(content, previous_messages)
        user_message = _persist_customer_message(
            session,
            conversation,
            principal,
            client_message_id=client_message_id,
            content=content,
            request_id=request_id,
            created_at=now,
        )
        run = RagRun(
            id=uuid4(),
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            kb_version_id=version.id,
            original_query=content,
            retrieval_query=retrieval_query,
            conversation_context_json=list(previous_messages),
            execution_trace_json=None,
            status="RUNNING",
            model_provider="deepseek",
            model_name=settings.chat_model,
            model_version=None,
            prompt_version=runtime.prompt_version,
            embedding_version=settings.active_embedding_model_revision,
            settings_version=runtime.settings_version,
        )
        session.add_all((user_message, run))
        record_conversation_event(
            session,
            conversation.id,
            "processing.started",
            {"message_id": str(user_message.id), "stage": "retrieval"},
            actor_type="SYSTEM",
            actor_id=None,
        )
        return user_message.id, run.id, previous_messages, runtime


def _fail_submission(engine: Engine, run_id: UUID, error_code: str) -> None:
    now = _now()
    with Session(engine) as session, session.begin():
        run = session.get(RagRun, run_id, with_for_update=True)
        if run is None or run.status != "RUNNING":
            return
        run.status = "FAILED"
        run.error_code = error_code
        run.completed_at = now
        record_conversation_event(
            session,
            run.conversation_id,
            "error",
            {"message_id": str(run.user_message_id), "code": error_code},
            actor_type="SYSTEM",
            actor_id=None,
        )


def _finish_submission(
    engine: Engine,
    principal: WidgetPrincipal,
    settings: Settings,
    *,
    conversation_id: UUID,
    user_message_id: UUID,
    run_id: UUID,
    answer: GroundedAnswer,
    execution_trace: dict[str, Any],
    embedding_version: str,
    request_id: UUID,
) -> SubmissionResult | ReviewPendingResult:
    now = _now()
    citations = [citation.model_dump(mode="json") for citation in answer.citations]
    with Session(engine) as session, session.begin():
        conversation = owned_conversation(session, principal, conversation_id, for_update=True)
        run = session.get(RagRun, run_id, with_for_update=True)
        if run is None or run.status != "RUNNING" or run.user_message_id != user_message_id:
            raise ConversationError(409, "MESSAGE_PROCESSING", "Message state changed")
        if conversation.state not in {"AI_ACTIVE", "RETURNED_TO_AI"}:
            run.status = "FAILED"
            run.error_code = "AI_CONTROL_LOST"
            run.completed_at = now
            record_conversation_event(
                session,
                conversation.id,
                "error",
                {"message_id": str(user_message_id), "code": "AI_CONTROL_LOST"},
                actor_type="SYSTEM",
                actor_id=None,
            )
            raise ConversationError(
                409,
                "AI_NOT_IN_CONTROL",
                "The assistant no longer controls this conversation",
            )
        version = session.scalar(
            select(KnowledgeBaseVersion).where(
                KnowledgeBaseVersion.dataset_id == answer.dataset_id,
                KnowledgeBaseVersion.dataset_version == answer.dataset_version,
            )
        )
        if version is None:
            raise ConversationError(503, "MESSAGE_FAILED", "Answer provenance is unavailable")
        review_required = settings.review_before_send_enabled and answer.status in {
            "ANSWERABLE",
            "PARTIALLY_ANSWERABLE",
        }
        assistant_message = Message(
            id=uuid4(),
            conversation_id=conversation.id,
            sender_type="AI",
            content=answer.answer,
            visibility="INTERNAL" if review_required else "PUBLIC",
            status="PENDING" if review_required else "DELIVERED",
            review_status="PENDING" if review_required else "NONE",
            review_regeneration_count=0,
            reply_to_message_id=user_message_id,
            citations_json=citations,
            delivered_at=None if review_required else now,
        )
        session.add(assistant_message)
        session.flush()
        run.assistant_message_id = assistant_message.id
        run.kb_version_id = version.id
        run.status = "COMPLETED"
        run.answerability_status = answer.status
        run.verification_status = answer.verification_status
        run.model_provider = answer.model.provider
        run.model_name = answer.model.name
        run.model_version = answer.model.version
        run.prompt_version = answer.model.prompt_version
        run.embedding_version = embedding_version
        run.execution_trace_json = execution_trace
        run.regenerated = answer.regenerated
        run.latency_ms = answer.duration_ms
        run.completed_at = now
        conversation.last_message_at = now
        conversation.updated_at = now
        if review_required:
            previous_state = conversation.state
            conversation.state = "AI_REVIEW_PENDING"
            record_conversation_event(
                session,
                conversation.id,
                "review.requested",
                {
                    "message_id": str(assistant_message.id),
                    "rag_run_id": str(run.id),
                    "from_state": previous_state,
                    "to_state": conversation.state,
                },
                actor_type="SYSTEM",
                actor_id=answer.model.name,
                visibility="INTERNAL",
            )
            record_conversation_event(
                session,
                conversation.id,
                "review.pending",
                {"from_state": previous_state, "to_state": conversation.state},
                actor_type="SYSTEM",
                actor_id=None,
            )
            record_audit_event(
                session,
                "review.requested",
                "message",
                assistant_message.id,
                actor_type="SYSTEM",
                actor_id=answer.model.name,
                request_id=request_id,
                before={"conversation_state": previous_state},
                after={
                    "conversation_state": conversation.state,
                    "review_status": "PENDING",
                    "rag_run_id": str(run.id),
                },
            )
            return ReviewPendingResult(conversation.id, assistant_message.id, run.id)
        record_conversation_event(
            session,
            conversation.id,
            "message.created",
            {
                "message_id": str(assistant_message.id),
                "rag_run_id": str(run.id),
                "sender": {"type": "AI", "label": settings.widget_assistant_label},
                "status": answer.status,
                "content": answer.answer,
                "citations": citations,
                "created_at": now.isoformat(),
            },
            actor_type="AI",
            actor_id=answer.model.name,
        )
        record_conversation_event(
            session,
            conversation.id,
            "message.delivered",
            {"message_id": str(assistant_message.id), "delivered_at": now.isoformat()},
            actor_type="SYSTEM",
            actor_id=None,
        )
        automatic_handoff = (
            (answer.status == "CONFLICTING_EVIDENCE" and settings.auto_handoff_on_conflict)
            or (answer.status == "NOT_ANSWERABLE" and settings.auto_handoff_on_not_answerable)
            or (answer.status == "PARTIALLY_ANSWERABLE" and settings.auto_handoff_on_partial)
        )
        if automatic_handoff:
            from app.handoffs import request_handoff_in_session

            request_handoff_in_session(
                session,
                conversation,
                reason=answer.status,
                priority="HIGH" if answer.status == "CONFLICTING_EVIDENCE" else "NORMAL",
                trigger_message_id=user_message_id,
                actor_type="SYSTEM",
                actor_id=answer.model.name,
                request_id=request_id,
            )
        return SubmissionResult(conversation.id, assistant_message.id, run.id, answer)


def submit_message(
    engine: Engine,
    principal: WidgetPrincipal,
    get_embedding_provider: Callable[[], EmbeddingProvider],
    get_llm_provider: Callable[[], LLMProvider],
    settings: Settings,
    *,
    conversation_id: UUID,
    client_message_id: UUID,
    content: str,
    request_id: UUID,
) -> MessageSubmissionResult:
    started = _start_submission(
        engine,
        principal,
        settings,
        conversation_id=conversation_id,
        client_message_id=client_message_id,
        content=content,
        request_id=request_id,
    )
    if isinstance(started, (SubmissionResult, QueuedSubmissionResult, ReviewPendingResult)):
        return started
    user_message_id, run_id, conversation_context, runtime = started
    try:
        embedding_provider = get_embedding_provider()
        llm_provider = get_llm_provider()
        execution = answer_knowledge_with_trace(
            engine,
            embedding_provider,
            llm_provider,
            runtime.effective_settings,
            runtime.prompts,
            content,
            conversation_context=conversation_context,
        )
        return _finish_submission(
            engine,
            principal,
            runtime.effective_settings,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            run_id=run_id,
            answer=execution.answer,
            execution_trace=execution.trace,
            embedding_version=embedding_provider.model_version,
            request_id=request_id,
        )
    except Exception as exc:
        if isinstance(exc, ConversationError):
            error_code = "AI_CONTROL_LOST" if exc.code == "AI_NOT_IN_CONTROL" else exc.code
        else:
            error_code = type(exc).__name__.upper()
        _fail_submission(engine, run_id, error_code)
        raise


def _transition(
    session: Session,
    conversation: Conversation,
    target: ConversationState,
    *,
    principal: WidgetPrincipal,
    request_id: UUID,
) -> None:
    current = conversation.state
    if target == current:
        return
    if target not in VALID_TRANSITIONS[current]:
        raise ConversationError(
            409,
            "INVALID_CONVERSATION_TRANSITION",
            f"Conversation cannot transition from {current} to {target}",
        )
    now = _now()
    conversation.state = target
    conversation.updated_at = now
    if target == "CLOSED":
        conversation.closed_at = now
        if current in {"HUMAN_REQUESTED", "HUMAN_ASSIGNED", "HUMAN_ACTIVE"}:
            session.add(
                HandoffEvent(
                    conversation_id=conversation.id,
                    event_type="CLOSE",
                    reason=conversation.handoff_reason,
                    from_state=current,
                    to_state=target,
                    actor_type="CUSTOMER",
                    actor_id=principal.anonymous_subject,
                    trigger_message_id=conversation.handoff_trigger_message_id,
                    request_id=request_id,
                )
            )
    record_conversation_event(
        session,
        conversation.id,
        "conversation.closed" if target == "CLOSED" else "conversation.state_changed",
        {"from_state": current, "to_state": target},
        actor_type="CUSTOMER",
        actor_id=principal.anonymous_subject,
    )
    record_audit_event(
        session,
        "conversation.state_changed",
        "conversation",
        conversation.id,
        actor_type="CUSTOMER",
        actor_id=principal.anonymous_subject,
        request_id=request_id,
        before={"state": current},
        after={"state": target},
    )


def close_conversation(
    engine: Engine,
    principal: WidgetPrincipal,
    conversation_id: UUID,
    *,
    request_id: UUID,
) -> ConversationSnapshot:
    with Session(engine) as session, session.begin():
        conversation = owned_conversation(session, principal, conversation_id, for_update=True)
        _transition(
            session,
            conversation,
            "CLOSED",
            principal=principal,
            request_id=request_id,
        )
        return conversation_snapshot(session, conversation)


def load_events(
    engine: Engine,
    principal: WidgetPrincipal,
    conversation_id: UUID,
    *,
    after_id: int,
    limit: int,
) -> tuple[EventRecord, ...]:
    with Session(engine) as session:
        owned_conversation(session, principal, conversation_id)
        rows = session.scalars(
            select(ConversationEvent)
            .where(
                ConversationEvent.conversation_id == conversation_id,
                ConversationEvent.visibility == "PUBLIC",
                ConversationEvent.id > after_id,
            )
            .order_by(ConversationEvent.id)
            .limit(limit + 1)
        ).all()
        if len(rows) > limit:
            raise ConversationError(
                409,
                "SSE_REPLAY_LIMIT_EXCEEDED",
                "Too many missed events; reload the conversation before reconnecting",
            )
        return tuple(
            EventRecord(
                event_id=event.id,
                conversation_id=event.conversation_id,
                event_type=event.event_type,
                payload=event.payload_json,
                created_at=event.created_at,
            )
            for event in rows
        )
