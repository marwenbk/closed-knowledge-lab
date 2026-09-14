from __future__ import annotations

import asyncio
import time
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.answering import AnswerabilityStatus, AnsweringError, Citation
from app.api import (
    ApiError,
    EngineDep,
    ErrorResponse,
    SettingsDep,
    embedding_provider,
    llm_provider,
)
from app.conversations import (
    ConversationError,
    ConversationSnapshot,
    EventRecord,
    QueuedSubmissionResult,
    ReviewPendingResult,
    SubmissionResult,
    WidgetPrincipal,
    authenticate_widget_session,
    close_conversation,
    create_or_restore_conversation,
    create_widget_session,
    get_conversation,
    load_events,
    submit_message,
)
from app.embeddings import EmbeddingError
from app.handoffs import HandoffState, request_customer_handoff
from app.llm import LLMError
from app.retrieval import RetrievalError

ERROR_RESPONSE = {"model": ErrorResponse}
bearer_scheme = HTTPBearer(auto_error=False)
widget_router = APIRouter(prefix="/api/v1/widget", tags=["widget"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


def _safe_text(value: str) -> str:
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError("text must not contain control characters")
    return value


class WidgetSessionRequest(StrictRequest):
    assistant_key: SecretStr
    anonymous_subject: str | None = Field(default=None, min_length=1, max_length=100)
    locale: str = Field(default="en-US", pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")

    @field_validator("anonymous_subject")
    @classmethod
    def validate_subject(cls, value: str | None) -> str | None:
        return _safe_text(value) if value is not None else None


class WidgetSessionResponse(BaseModel):
    session_id: UUID
    token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_at: datetime
    locale: str


class ConversationRequest(StrictRequest):
    conversation_id: UUID | None = Field(default=None, strict=False)


class MessageRequest(StrictRequest):
    content: str = Field(min_length=1, max_length=1000)
    client_message_id: UUID = Field(strict=False)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        return _safe_text(value)


class SenderResponse(BaseModel):
    type: str
    label: str


class PublicMessageResponse(BaseModel):
    message_id: UUID
    rag_run_id: UUID | None
    sender: SenderResponse
    content: str
    status: str
    citations: tuple[Citation, ...]
    created_at: datetime
    delivered_at: datetime | None


class ConversationResponse(BaseModel):
    conversation_id: UUID
    state: str
    messages: tuple[PublicMessageResponse, ...]
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    delivery_mode: Literal["AI"] = "AI"
    conversation_id: UUID
    message_id: UUID
    rag_run_id: UUID
    status: AnswerabilityStatus
    answer: str
    sender: SenderResponse
    citations: tuple[Citation, ...]


class HumanQueueMessageResponse(BaseModel):
    delivery_mode: Literal["HUMAN_QUEUE"] = "HUMAN_QUEUE"
    conversation_id: UUID
    message_id: UUID
    rag_run_id: None = None
    status: Literal["PERSISTED"] = "PERSISTED"
    state: str


class ReviewPendingMessageResponse(BaseModel):
    delivery_mode: Literal["REVIEW_PENDING"] = "REVIEW_PENDING"
    conversation_id: UUID
    message_id: UUID
    rag_run_id: UUID
    status: Literal["PENDING_REVIEW"] = "PENDING_REVIEW"
    state: Literal["AI_REVIEW_PENDING"] = "AI_REVIEW_PENDING"


class HandoffResponse(BaseModel):
    conversation_id: UUID
    state: str
    priority: str
    reason: str
    requested_at: datetime
    assigned_agent_id: UUID | None
    claimed_at: datetime | None


@dataclass(frozen=True)
class EventSubscription:
    principal: WidgetPrincipal
    initial_events: tuple[EventRecord, ...]
    initial_cursor: int


def _request_id(request: Request) -> UUID:
    return UUID(str(request.state.request_id))


def _api_error(exc: ConversationError) -> ApiError:
    return ApiError(exc.status_code, exc.code, str(exc))


def _principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    engine: EngineDep,
    settings: SettingsDep,
    origin: Annotated[str | None, Header(alias="Origin")] = None,
) -> WidgetPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer" or origin is None:
        raise ApiError(401, "WIDGET_AUTH_REQUIRED", "A valid widget session is required")
    try:
        return authenticate_widget_session(
            engine,
            settings,
            token=credentials.credentials,
            origin=origin,
        )
    except ConversationError as exc:
        raise _api_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc


WidgetPrincipalDep = Annotated[WidgetPrincipal, Depends(_principal)]


def _sender(sender_type: str, assistant_label: str) -> SenderResponse:
    labels = {"CUSTOMER": "You", "AI": assistant_label, "HUMAN": "Human support"}
    return SenderResponse(type=sender_type, label=labels.get(sender_type, assistant_label))


def _conversation_response(
    snapshot: ConversationSnapshot,
    assistant_label: str,
) -> ConversationResponse:
    return ConversationResponse(
        conversation_id=snapshot.conversation_id,
        state=snapshot.state,
        messages=tuple(
            PublicMessageResponse(
                message_id=message.message_id,
                rag_run_id=message.rag_run_id,
                sender=_sender(message.sender_type, assistant_label),
                content=message.content,
                status=message.status,
                citations=tuple(Citation.model_validate(value) for value in message.citations),
                created_at=message.created_at,
                delivered_at=message.delivered_at,
            )
            for message in snapshot.messages
        ),
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )


def _message_response(
    result: SubmissionResult | QueuedSubmissionResult | ReviewPendingResult,
    assistant_label: str,
) -> MessageResponse | HumanQueueMessageResponse | ReviewPendingMessageResponse:
    if isinstance(result, QueuedSubmissionResult):
        return HumanQueueMessageResponse(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            state=result.state,
        )
    if isinstance(result, ReviewPendingResult):
        return ReviewPendingMessageResponse(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            rag_run_id=result.rag_run_id,
        )
    return MessageResponse(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        rag_run_id=result.rag_run_id,
        status=result.answer.status,
        answer=result.answer.answer,
        sender=SenderResponse(type="AI", label=assistant_label),
        citations=result.answer.citations,
    )


def _handoff_response(state: HandoffState) -> HandoffResponse:
    return HandoffResponse(
        conversation_id=state.conversation_id,
        state=state.state,
        priority=state.priority,
        reason=state.reason,
        requested_at=state.requested_at,
        assigned_agent_id=state.assigned_agent_id,
        claimed_at=state.claimed_at,
    )


@widget_router.post(
    "/sessions",
    status_code=201,
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
        500: ERROR_RESPONSE,
    },
)
def start_session(
    payload: WidgetSessionRequest,
    origin: Annotated[str, Header(alias="Origin")],
    engine: EngineDep,
    settings: SettingsDep,
) -> WidgetSessionResponse:
    try:
        result = create_widget_session(
            engine,
            settings,
            assistant_key=payload.assistant_key.get_secret_value(),
            origin=origin,
            anonymous_subject=payload.anonymous_subject,
            locale=payload.locale,
        )
    except ConversationError as exc:
        raise _api_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return WidgetSessionResponse(
        session_id=result.session_id,
        token=result.token,
        expires_at=result.expires_at,
        locale=result.locale,
    )


@widget_router.post(
    "/conversations",
    status_code=201,
    responses={
        200: {"model": ConversationResponse},
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
def start_conversation(
    request: Request,
    payload: ConversationRequest,
    response: Response,
    principal: WidgetPrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> ConversationResponse:
    try:
        snapshot, created = create_or_restore_conversation(
            engine,
            principal,
            conversation_id=payload.conversation_id,
            request_id=_request_id(request),
        )
    except ConversationError as exc:
        raise _api_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    response.status_code = 201 if created else 200
    return _conversation_response(snapshot, settings.widget_assistant_label)


@widget_router.get(
    "/conversations/{conversation_id}",
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
def read_conversation(
    conversation_id: Annotated[UUID, Path()],
    principal: WidgetPrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> ConversationResponse:
    try:
        snapshot = get_conversation(engine, principal, conversation_id)
    except ConversationError as exc:
        raise _api_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return _conversation_response(snapshot, settings.widget_assistant_label)


@widget_router.post(
    "/conversations/{conversation_id}/messages",
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
        500: ERROR_RESPONSE,
    },
)
def send_message(
    request: Request,
    conversation_id: Annotated[UUID, Path()],
    payload: MessageRequest,
    principal: WidgetPrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> MessageResponse | HumanQueueMessageResponse | ReviewPendingMessageResponse:
    try:
        result = submit_message(
            engine,
            principal,
            lambda: embedding_provider(request),
            lambda: llm_provider(request),
            settings,
            conversation_id=conversation_id,
            client_message_id=payload.client_message_id,
            content=payload.content,
            request_id=_request_id(request),
        )
    except ConversationError as exc:
        raise _api_error(exc) from exc
    except LLMError as exc:
        raise ApiError(503, "LLM_NOT_READY", "The answer provider is unavailable") from exc
    except (AnsweringError, EmbeddingError, RetrievalError) as exc:
        raise ApiError(503, "ANSWERING_NOT_READY", "The answer pipeline is unavailable") from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return _message_response(result, settings.widget_assistant_label)


@widget_router.post(
    "/conversations/{conversation_id}/request-human",
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
def request_human(
    request: Request,
    conversation_id: Annotated[UUID, Path()],
    principal: WidgetPrincipalDep,
    engine: EngineDep,
) -> HandoffResponse:
    try:
        _, state = request_customer_handoff(
            engine,
            principal,
            conversation_id,
            request_id=_request_id(request),
        )
    except ConversationError as exc:
        raise _api_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return _handoff_response(state)


@widget_router.post(
    "/conversations/{conversation_id}/close",
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
def close(
    request: Request,
    conversation_id: Annotated[UUID, Path()],
    principal: WidgetPrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> ConversationResponse:
    try:
        snapshot = close_conversation(
            engine,
            principal,
            conversation_id,
            request_id=_request_id(request),
        )
    except ConversationError as exc:
        raise _api_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return _conversation_response(snapshot, settings.widget_assistant_label)


def _server_event(event: EventRecord) -> ServerSentEvent:
    return ServerSentEvent(
        id=str(event.event_id),
        event=event.event_type,
        retry=1_000,
        data={
            "event_id": event.event_id,
            "conversation_id": str(event.conversation_id),
            "timestamp": event.created_at.isoformat(),
            "payload": event.payload,
        },
    )


async def event_stream(
    request: Request,
    engine: Engine,
    principal: WidgetPrincipal,
    conversation_id: UUID,
    initial_events: tuple[EventRecord, ...],
    *,
    initial_cursor: int,
    poll_interval: float,
    keepalive_interval: float,
    replay_limit: int,
) -> AsyncIterator[ServerSentEvent]:
    cursor = initial_cursor
    pending = initial_events
    keepalive_at = time.monotonic() + keepalive_interval
    while _now() < principal.expires_at and not await request.is_disconnected():
        for event in pending:
            cursor = event.event_id
            yield _server_event(event)
        pending = ()
        if time.monotonic() >= keepalive_at:
            yield ServerSentEvent(
                event="keepalive",
                data={"timestamp": datetime.now(UTC).isoformat()},
            )
            keepalive_at = time.monotonic() + keepalive_interval
        await asyncio.sleep(poll_interval)
        try:
            pending = await asyncio.to_thread(
                load_events,
                engine,
                principal,
                conversation_id,
                after_id=cursor,
                limit=replay_limit,
            )
        except (ConversationError, SQLAlchemyError):
            yield ServerSentEvent(event="error", data={"code": "EVENT_STREAM_UNAVAILABLE"})
            return


def _event_subscription(
    response: Response,
    conversation_id: Annotated[UUID, Path()],
    principal: WidgetPrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID", ge=0)] = None,
) -> EventSubscription:
    cursor = last_event_id or 0
    try:
        initial_events = load_events(
            engine,
            principal,
            conversation_id,
            after_id=cursor,
            limit=settings.sse_replay_limit,
        )
    except ConversationError as exc:
        raise _api_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    return EventSubscription(principal, initial_events, cursor)


EventSubscriptionDep = Annotated[EventSubscription, Depends(_event_subscription)]


@widget_router.get(
    "/conversations/{conversation_id}/events",
    response_class=EventSourceResponse,
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
async def events(
    request: Request,
    conversation_id: Annotated[UUID, Path()],
    subscription: EventSubscriptionDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> AsyncIterator[ServerSentEvent]:
    async for event in event_stream(
        request,
        engine,
        subscription.principal,
        conversation_id,
        subscription.initial_events,
        initial_cursor=subscription.initial_cursor,
        poll_interval=settings.sse_poll_interval_seconds,
        keepalive_interval=settings.sse_keepalive_seconds,
        replay_limit=settings.sse_replay_limit,
    ):
        yield event


def _now() -> datetime:
    return datetime.now(UTC)
