from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Engine, case, func, select, update
from sqlalchemy.orm import Session

from app.admin_auth import AdminPrincipal
from app.conversations import (
    ConversationError,
    ConversationSnapshot,
    WidgetPrincipal,
    conversation_snapshot,
    owned_conversation,
    record_audit_event,
    record_conversation_event,
)
from app.models import (
    AdminUser,
    Conversation,
    ConversationEvent,
    HandoffEvent,
    Message,
    RagRun,
)

HUMAN_STATES = frozenset({"HUMAN_REQUESTED", "HUMAN_ASSIGNED", "HUMAN_ACTIVE"})
REQUESTABLE_STATES = frozenset({"AI_ACTIVE", "AI_REVIEW_PENDING", "RETURNED_TO_AI"})
SUPERVISOR_ROLES = frozenset({"ADMIN", "SUPERVISOR"})


@dataclass(frozen=True)
class HandoffState:
    conversation_id: UUID
    state: str
    priority: str
    reason: str
    requested_at: datetime
    assigned_agent_id: UUID | None
    claimed_at: datetime | None


@dataclass(frozen=True)
class HandoffQueueItem(HandoffState):
    waiting_seconds: int
    latest_customer_message: str | None
    assigned_agent_name: str | None
    answerability_status: str | None


@dataclass(frozen=True)
class HandoffQueue:
    items: tuple[HandoffQueueItem, ...]
    total: int


@dataclass(frozen=True)
class AdminMessageRecord:
    message_id: UUID
    client_message_id: UUID | None
    sender_type: str
    sender_user_id: UUID | None
    sender_label: str
    content: str
    visibility: str
    status: str
    review_status: str
    review_regeneration_count: int
    citations: tuple[dict[str, Any], ...]
    rag_run_id: UUID | None
    created_at: datetime
    delivered_at: datetime | None


@dataclass(frozen=True)
class RagRunSummary:
    rag_run_id: UUID
    status: str
    answerability_status: str | None
    verification_status: str | None
    error_code: str | None


@dataclass(frozen=True)
class AdminConversation:
    conversation_id: UUID
    state: str
    priority: str | None
    handoff_reason: str | None
    handoff_requested_at: datetime | None
    assigned_agent_id: UUID | None
    assigned_agent_name: str | None
    claimed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    messages: tuple[AdminMessageRecord, ...]
    rag_runs: tuple[RagRunSummary, ...]


@dataclass(frozen=True)
class AdminEventRecord:
    event_id: int
    conversation_id: UUID
    event_type: str
    visibility: str
    payload: dict[str, Any]
    created_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _state(conversation: Conversation) -> HandoffState:
    if (
        conversation.priority is None
        or conversation.handoff_reason is None
        or conversation.handoff_requested_at is None
    ):
        raise ConversationError(503, "HANDOFF_STATE_INVALID", "Handoff state is incomplete")
    return HandoffState(
        conversation.id,
        conversation.state,
        conversation.priority,
        conversation.handoff_reason,
        conversation.handoff_requested_at,
        conversation.assigned_agent_id,
        conversation.claimed_at,
    )


def _handoff_event(
    session: Session,
    conversation: Conversation,
    event_type: str,
    *,
    reason: str | None,
    from_state: str,
    actor_type: str,
    actor_id: str | None,
    trigger_message_id: UUID | None,
    request_id: UUID,
) -> None:
    session.add(
        HandoffEvent(
            conversation_id=conversation.id,
            event_type=event_type,
            reason=reason,
            from_state=from_state,
            to_state=conversation.state,
            actor_type=actor_type,
            actor_id=actor_id,
            trigger_message_id=trigger_message_id,
            request_id=request_id,
        )
    )


def request_handoff_in_session(
    session: Session,
    conversation: Conversation,
    *,
    reason: str,
    priority: str,
    trigger_message_id: UUID | None,
    actor_type: str,
    actor_id: str | None,
    request_id: UUID,
) -> HandoffState:
    if conversation.state in HUMAN_STATES:
        return _state(conversation)
    if conversation.state not in REQUESTABLE_STATES:
        code = "CONVERSATION_CLOSED" if conversation.state == "CLOSED" else "HANDOFF_NOT_ALLOWED"
        raise ConversationError(409, code, "A handoff cannot be requested in the current state")
    previous = conversation.state
    now = _now()
    if previous == "AI_REVIEW_PENDING":
        proposal = session.scalar(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.review_status.in_(("PENDING", "REGENERATING")),
            )
            .order_by(Message.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        if proposal is not None:
            proposal.review_status = "REJECTED"
            proposal.status = "FAILED"
            record_conversation_event(
                session,
                conversation.id,
                "review.cancelled",
                {"message_id": str(proposal.id), "reason": "HANDOFF_REQUESTED"},
                actor_type=actor_type,
                actor_id=actor_id,
                visibility="INTERNAL",
            )
    conversation.state = "HUMAN_REQUESTED"
    conversation.priority = priority
    conversation.handoff_reason = reason
    conversation.handoff_trigger_message_id = trigger_message_id
    conversation.handoff_requested_at = now
    conversation.assigned_agent_id = None
    conversation.claimed_at = None
    conversation.updated_at = now
    _handoff_event(
        session,
        conversation,
        "REQUEST",
        reason=reason,
        from_state=previous,
        actor_type=actor_type,
        actor_id=actor_id,
        trigger_message_id=trigger_message_id,
        request_id=request_id,
    )
    record_conversation_event(
        session,
        conversation.id,
        "handoff.requested",
        {
            "from_state": previous,
            "to_state": conversation.state,
            "reason": reason,
            "priority": priority,
            "requested_at": now.isoformat(),
        },
        actor_type=actor_type,
        actor_id=actor_id,
    )
    record_audit_event(
        session,
        "handoff.requested",
        "conversation",
        conversation.id,
        actor_type=actor_type,
        actor_id=actor_id,
        request_id=request_id,
        before={"state": previous},
        after={"state": conversation.state, "reason": reason, "priority": priority},
    )
    return _state(conversation)


def request_customer_handoff(
    engine: Engine,
    principal: WidgetPrincipal,
    conversation_id: UUID,
    *,
    request_id: UUID,
) -> tuple[ConversationSnapshot, HandoffState]:
    with Session(engine) as session, session.begin():
        conversation = owned_conversation(session, principal, conversation_id, for_update=True)
        trigger_message_id = session.scalar(
            select(Message.id)
            .where(
                Message.conversation_id == conversation.id,
                Message.sender_type == "CUSTOMER",
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
        state = request_handoff_in_session(
            session,
            conversation,
            reason="CUSTOMER_REQUEST",
            priority="NORMAL",
            trigger_message_id=trigger_message_id,
            actor_type="CUSTOMER",
            actor_id=principal.anonymous_subject,
            request_id=request_id,
        )
        return conversation_snapshot(session, conversation), state


def list_handoffs(
    engine: Engine,
    principal: AdminPrincipal,
    *,
    state: str | None,
    assignment: str,
    reason: str | None,
    priority: str | None,
    waiting_at_least_seconds: int | None,
    offset: int,
    limit: int,
) -> HandoffQueue:
    now = _now()
    conditions: list[Any] = [Conversation.state.in_(HUMAN_STATES)]
    if state:
        conditions.append(Conversation.state == state)
    if assignment == "unassigned":
        conditions.append(Conversation.assigned_agent_id.is_(None))
    elif assignment == "me":
        conditions.append(Conversation.assigned_agent_id == principal.user_id)
    if reason:
        conditions.append(Conversation.handoff_reason == reason)
    if priority:
        conditions.append(Conversation.priority == priority)
    if waiting_at_least_seconds is not None:
        conditions.append(
            Conversation.handoff_requested_at <= now - timedelta(seconds=waiting_at_least_seconds)
        )
    latest_customer = (
        select(Message.content)
        .where(
            Message.conversation_id == Conversation.id,
            Message.sender_type == "CUSTOMER",
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    latest_answerability = (
        select(RagRun.answerability_status)
        .where(RagRun.conversation_id == Conversation.id)
        .order_by(RagRun.created_at.desc(), RagRun.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    assigned_name = (
        select(AdminUser.display_name)
        .where(AdminUser.id == Conversation.assigned_agent_id)
        .scalar_subquery()
    )
    priority_order = case(
        (Conversation.priority == "URGENT", 0),
        (Conversation.priority == "HIGH", 1),
        (Conversation.priority == "NORMAL", 2),
        else_=3,
    )
    with Session(engine) as session:
        total = (
            session.scalar(select(func.count()).select_from(Conversation).where(*conditions)) or 0
        )
        rows = session.execute(
            select(
                Conversation,
                latest_customer.label("latest_customer"),
                assigned_name.label("assigned_name"),
                latest_answerability.label("answerability"),
            )
            .where(*conditions)
            .order_by(priority_order, Conversation.handoff_requested_at, Conversation.id)
            .offset(offset)
            .limit(limit)
        ).all()
        items = tuple(
            HandoffQueueItem(
                conversation_id=conversation.id,
                state=conversation.state,
                priority=conversation.priority,
                reason=conversation.handoff_reason,
                requested_at=conversation.handoff_requested_at,
                assigned_agent_id=conversation.assigned_agent_id,
                claimed_at=conversation.claimed_at,
                waiting_seconds=max(
                    0,
                    int((now - conversation.handoff_requested_at).total_seconds()),
                ),
                latest_customer_message=customer_message,
                assigned_agent_name=agent_name,
                answerability_status=answerability,
            )
            for conversation, customer_message, agent_name, answerability in rows
            if conversation.priority is not None
            and conversation.handoff_reason is not None
            and conversation.handoff_requested_at is not None
        )
        return HandoffQueue(items, total)


def claim_handoff(
    engine: Engine,
    principal: AdminPrincipal,
    conversation_id: UUID,
    *,
    request_id: UUID,
) -> HandoffState:
    now = _now()
    with Session(engine) as session, session.begin():
        claimed_id = session.scalar(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.state == "HUMAN_REQUESTED",
                Conversation.assigned_agent_id.is_(None),
            )
            .values(
                state="HUMAN_ASSIGNED",
                assigned_agent_id=principal.user_id,
                claimed_at=now,
                updated_at=now,
            )
            .returning(Conversation.id)
        )
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise ConversationError(404, "CONVERSATION_NOT_FOUND", "Conversation not found")
        if claimed_id is None:
            if conversation.assigned_agent_id == principal.user_id and conversation.state in {
                "HUMAN_ASSIGNED",
                "HUMAN_ACTIVE",
            }:
                return _state(conversation)
            code = (
                "HANDOFF_ALREADY_CLAIMED"
                if conversation.assigned_agent_id is not None
                else "HANDOFF_NOT_CLAIMABLE"
            )
            raise ConversationError(409, code, "The handoff is no longer available")
        _handoff_event(
            session,
            conversation,
            "CLAIM",
            reason=conversation.handoff_reason,
            from_state="HUMAN_REQUESTED",
            actor_type="ADMIN_USER",
            actor_id=str(principal.user_id),
            trigger_message_id=conversation.handoff_trigger_message_id,
            request_id=request_id,
        )
        record_conversation_event(
            session,
            conversation.id,
            "handoff.assigned",
            {
                "from_state": "HUMAN_REQUESTED",
                "to_state": conversation.state,
                "agent_label": principal.display_name,
                "claimed_at": now.isoformat(),
            },
            actor_type="ADMIN_USER",
            actor_id=str(principal.user_id),
        )
        record_audit_event(
            session,
            "handoff.claimed",
            "conversation",
            conversation.id,
            actor_type="ADMIN_USER",
            actor_id=str(principal.user_id),
            request_id=request_id,
            before={"state": "HUMAN_REQUESTED", "assigned_agent_id": None},
            after={
                "state": conversation.state,
                "assigned_agent_id": str(principal.user_id),
            },
        )
        return _state(conversation)


def _can_override(principal: AdminPrincipal) -> bool:
    return not principal.roles.isdisjoint(SUPERVISOR_ROLES)


def _require_assignee(conversation: Conversation, principal: AdminPrincipal) -> None:
    if conversation.assigned_agent_id != principal.user_id:
        raise ConversationError(
            403,
            "HANDOFF_NOT_ASSIGNED",
            "The handoff is assigned to another agent",
        )


def add_admin_message(
    engine: Engine,
    principal: AdminPrincipal,
    conversation_id: UUID,
    *,
    client_message_id: UUID,
    content: str,
    visibility: str,
    request_id: UUID,
) -> AdminMessageRecord:
    now = _now()
    with Session(engine) as session, session.begin():
        conversation = session.get(Conversation, conversation_id, with_for_update=True)
        if conversation is None:
            raise ConversationError(404, "CONVERSATION_NOT_FOUND", "Conversation not found")
        existing = session.scalar(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.client_message_id == client_message_id,
            )
        )
        if existing is not None:
            if (
                existing.content != content
                or existing.visibility != visibility
                or existing.sender_user_id != principal.user_id
            ):
                raise ConversationError(
                    409,
                    "IDEMPOTENCY_KEY_REUSED",
                    "The client message ID was already used with different content",
                )
            return _admin_message(existing, principal.display_name, None)
        if conversation.state not in HUMAN_STATES:
            raise ConversationError(409, "HANDOFF_NOT_ACTIVE", "The handoff is not active")
        if visibility == "PUBLIC":
            _require_assignee(conversation, principal)
            sender_type = "HUMAN"
            status = "DELIVERED"
            delivered_at = now
        else:
            if not _can_override(principal):
                _require_assignee(conversation, principal)
            sender_type = "INTERNAL"
            status = "PERSISTED"
            delivered_at = None
        previous = conversation.state
        message = Message(
            id=uuid4(),
            conversation_id=conversation.id,
            client_message_id=client_message_id,
            sender_type=sender_type,
            sender_user_id=principal.user_id,
            content=content,
            visibility=visibility,
            status=status,
            citations_json=[],
            delivered_at=delivered_at,
        )
        session.add(message)
        session.flush()
        if visibility == "PUBLIC" and conversation.state == "HUMAN_ASSIGNED":
            conversation.state = "HUMAN_ACTIVE"
            _handoff_event(
                session,
                conversation,
                "START",
                reason=conversation.handoff_reason,
                from_state=previous,
                actor_type="ADMIN_USER",
                actor_id=str(principal.user_id),
                trigger_message_id=message.id,
                request_id=request_id,
            )
            record_conversation_event(
                session,
                conversation.id,
                "handoff.started",
                {
                    "from_state": previous,
                    "to_state": conversation.state,
                    "agent_label": principal.display_name,
                },
                actor_type="ADMIN_USER",
                actor_id=str(principal.user_id),
            )
        conversation.last_message_at = now
        conversation.updated_at = now
        event_name = "message.created" if visibility == "PUBLIC" else "note.created"
        payload = {
            "message_id": str(message.id),
            "sender": {"type": sender_type, "label": principal.display_name},
            "content": content,
            "citations": [],
            "created_at": now.isoformat(),
        }
        record_conversation_event(
            session,
            conversation.id,
            event_name,
            payload,
            actor_type="ADMIN_USER",
            actor_id=str(principal.user_id),
            visibility=visibility,
        )
        if visibility == "PUBLIC":
            record_conversation_event(
                session,
                conversation.id,
                "message.delivered",
                {"message_id": str(message.id), "delivered_at": now.isoformat()},
                actor_type="ADMIN_USER",
                actor_id=str(principal.user_id),
            )
        _handoff_event(
            session,
            conversation,
            "MESSAGE" if visibility == "PUBLIC" else "NOTE",
            reason=conversation.handoff_reason,
            from_state=previous,
            actor_type="ADMIN_USER",
            actor_id=str(principal.user_id),
            trigger_message_id=message.id,
            request_id=request_id,
        )
        record_audit_event(
            session,
            "message.human_created" if visibility == "PUBLIC" else "message.note_created",
            "message",
            message.id,
            actor_type="ADMIN_USER",
            actor_id=str(principal.user_id),
            request_id=request_id,
            after={
                "conversation_id": str(conversation.id),
                "visibility": visibility,
                "status": status,
            },
        )
        return _admin_message(message, principal.display_name, None)


def _admin_message(
    message: Message,
    sender_name: str | None,
    rag_run_id: UUID | None,
) -> AdminMessageRecord:
    labels = {
        "CUSTOMER": "Cliente",
        "AI": "Closed-Knowledge Lab",
        "SYSTEM": "Closed-Knowledge Lab",
    }
    return AdminMessageRecord(
        message_id=message.id,
        client_message_id=message.client_message_id,
        sender_type=message.sender_type,
        sender_user_id=message.sender_user_id,
        sender_label=sender_name or labels.get(message.sender_type, "Atendimento humano"),
        content=message.content,
        visibility=message.visibility,
        status=message.status,
        review_status=message.review_status,
        review_regeneration_count=message.review_regeneration_count,
        citations=tuple(message.citations_json),
        rag_run_id=rag_run_id,
        created_at=message.created_at,
        delivered_at=message.delivered_at,
    )


def get_admin_conversation(engine: Engine, conversation_id: UUID) -> AdminConversation:
    with Session(engine) as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise ConversationError(404, "CONVERSATION_NOT_FOUND", "Conversation not found")
        assigned_name = (
            session.scalar(
                select(AdminUser.display_name).where(AdminUser.id == conversation.assigned_agent_id)
            )
            if conversation.assigned_agent_id
            else None
        )
        rows = session.execute(
            select(Message, AdminUser.display_name, RagRun.id)
            .outerjoin(AdminUser, AdminUser.id == Message.sender_user_id)
            .outerjoin(
                RagRun,
                (RagRun.user_message_id == Message.id)
                | (RagRun.assistant_message_id == Message.id),
            )
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at, Message.id)
        ).all()
        runs = session.scalars(
            select(RagRun)
            .where(RagRun.conversation_id == conversation_id)
            .order_by(RagRun.created_at, RagRun.id)
        ).all()
        return AdminConversation(
            conversation_id=conversation.id,
            state=conversation.state,
            priority=conversation.priority,
            handoff_reason=conversation.handoff_reason,
            handoff_requested_at=conversation.handoff_requested_at,
            assigned_agent_id=conversation.assigned_agent_id,
            assigned_agent_name=assigned_name,
            claimed_at=conversation.claimed_at,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            messages=tuple(_admin_message(message, name, run_id) for message, name, run_id in rows),
            rag_runs=tuple(
                RagRunSummary(
                    run.id,
                    run.status,
                    run.answerability_status,
                    run.verification_status,
                    run.error_code,
                )
                for run in runs
            ),
        )


def _require_control(conversation: Conversation, principal: AdminPrincipal) -> None:
    if not _can_override(principal):
        _require_assignee(conversation, principal)


def transition_handoff(
    engine: Engine,
    principal: AdminPrincipal,
    conversation_id: UUID,
    *,
    target: Literal["RETURNED_TO_AI", "CLOSED"],
    request_id: UUID,
) -> HandoffState:
    now = _now()
    with Session(engine) as session, session.begin():
        conversation = session.get(Conversation, conversation_id, with_for_update=True)
        if conversation is None:
            raise ConversationError(404, "CONVERSATION_NOT_FOUND", "Conversation not found")
        if target == conversation.state:
            return _state(conversation)
        if conversation.state not in HUMAN_STATES:
            raise ConversationError(409, "HANDOFF_NOT_ACTIVE", "The handoff is not active")
        _require_control(conversation, principal)
        previous = conversation.state
        previous_assignee = conversation.assigned_agent_id
        conversation.state = target
        conversation.updated_at = now
        if target == "RETURNED_TO_AI":
            conversation.assigned_agent_id = None
            conversation.claimed_at = None
            event_type = "RETURN"
            public_event = "handoff.returned_to_ai"
        else:
            conversation.closed_at = now
            event_type = "CLOSE"
            public_event = "conversation.closed"
        _handoff_event(
            session,
            conversation,
            event_type,
            reason=conversation.handoff_reason,
            from_state=previous,
            actor_type="ADMIN_USER",
            actor_id=str(principal.user_id),
            trigger_message_id=conversation.handoff_trigger_message_id,
            request_id=request_id,
        )
        record_conversation_event(
            session,
            conversation.id,
            public_event,
            {"from_state": previous, "to_state": target},
            actor_type="ADMIN_USER",
            actor_id=str(principal.user_id),
        )
        record_audit_event(
            session,
            "handoff.returned_to_ai" if target == "RETURNED_TO_AI" else "conversation.closed",
            "conversation",
            conversation.id,
            actor_type="ADMIN_USER",
            actor_id=str(principal.user_id),
            request_id=request_id,
            before={
                "state": previous,
                "assigned_agent_id": (
                    str(previous_assignee) if previous_assignee is not None else None
                ),
            },
            after={
                "state": target,
                "assigned_agent_id": (
                    str(conversation.assigned_agent_id)
                    if conversation.assigned_agent_id is not None
                    else None
                ),
            },
        )
        return _state(conversation)


def load_admin_events(
    engine: Engine,
    *,
    after_id: int,
    limit: int,
) -> tuple[AdminEventRecord, ...]:
    with Session(engine) as session:
        rows = session.scalars(
            select(ConversationEvent)
            .where(ConversationEvent.id > after_id)
            .order_by(ConversationEvent.id)
            .limit(limit + 1)
        ).all()
        if len(rows) > limit:
            raise ConversationError(
                409,
                "SSE_REPLAY_LIMIT_EXCEEDED",
                "Too many missed events; reload the queue before reconnecting",
            )
        return tuple(
            AdminEventRecord(
                event.id,
                event.conversation_id,
                event.event_type,
                event.visibility,
                event.payload_json,
                event.created_at,
            )
            for event in rows
        )


def latest_admin_event_id(engine: Engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.coalesce(func.max(ConversationEvent.id), 0))) or 0
