from __future__ import annotations

import asyncio
import time
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, Path, Query, Request, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.admin_auth import (
    ADMIN_CSRF_COOKIE,
    ADMIN_SESSION_COOKIE,
    AdminAuthError,
    AdminPrincipal,
    authenticate_admin,
    login_admin,
    require_takeover_role,
    revoke_admin_session,
    verify_csrf,
)
from app.api import ApiError, EngineDep, ErrorResponse, SettingsDep
from app.config import Settings
from app.conversations import ConversationError, normalize_origin
from app.handoffs import (
    AdminConversation,
    AdminEventRecord,
    AdminMessageRecord,
    HandoffQueue,
    HandoffState,
    add_admin_message,
    claim_handoff,
    get_admin_conversation,
    list_handoffs,
    load_admin_events,
    transition_handoff,
)

ERROR_RESPONSE = {"model": ErrorResponse}
auth_router = APIRouter(prefix="/api/v1/admin/auth", tags=["admin-auth"])
admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class DomainResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(StrictRequest):
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=128)


class AdminIdentityResponse(DomainResponse):
    user_id: UUID
    email: str
    display_name: str
    roles: tuple[str, ...]
    expires_at: datetime


class HandoffStateResponse(DomainResponse):
    conversation_id: UUID
    state: str
    priority: str
    reason: str
    requested_at: datetime
    assigned_agent_id: UUID | None
    claimed_at: datetime | None


class HandoffQueueItemResponse(HandoffStateResponse):
    waiting_seconds: int
    latest_customer_message: str | None
    assigned_agent_name: str | None
    answerability_status: str | None


class HandoffQueueResponse(BaseModel):
    items: tuple[HandoffQueueItemResponse, ...]
    total: int
    offset: int
    limit: int


class AdminMessageResponse(DomainResponse):
    message_id: UUID
    client_message_id: UUID | None
    sender_type: str
    sender_user_id: UUID | None
    sender_label: str
    content: str
    visibility: str
    status: str
    citations: tuple[dict[str, object], ...]
    rag_run_id: UUID | None
    created_at: datetime
    delivered_at: datetime | None


class RagRunSummaryResponse(DomainResponse):
    rag_run_id: UUID
    status: str
    answerability_status: str | None
    verification_status: str | None
    error_code: str | None


class AdminConversationResponse(DomainResponse):
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
    messages: tuple[AdminMessageResponse, ...]
    rag_runs: tuple[RagRunSummaryResponse, ...]


class AdminMessageRequest(StrictRequest):
    content: str = Field(min_length=1, max_length=4000)
    client_message_id: UUID = Field(strict=False)
    visibility: Literal["PUBLIC", "INTERNAL"]

    @field_validator("content")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise ValueError("content must not contain control characters")
        return value


def _request_id(request: Request) -> UUID:
    return UUID(str(request.state.request_id))


def _auth_error(exc: AdminAuthError) -> ApiError:
    return ApiError(exc.status_code, exc.code, str(exc))


def _conversation_error(exc: ConversationError) -> ApiError:
    return ApiError(exc.status_code, exc.code, str(exc))


def _require_origin(origin: str | None, settings: Settings) -> str:
    if origin is None:
        raise ApiError(403, "ADMIN_ORIGIN_NOT_ALLOWED", "The admin origin is not allowed")
    try:
        normalized = normalize_origin(origin)
        allowed = {normalize_origin(value) for value in settings.allowed_admin_origins}
    except ConversationError as exc:
        raise ApiError(403, "ADMIN_ORIGIN_NOT_ALLOWED", "The admin origin is not allowed") from exc
    if normalized not in allowed:
        raise ApiError(403, "ADMIN_ORIGIN_NOT_ALLOWED", "The admin origin is not allowed")
    return normalized


def _admin_principal(
    engine: EngineDep,
    settings: SettingsDep,
    session_token: Annotated[str | None, Cookie(alias=ADMIN_SESSION_COOKIE)] = None,
    origin: Annotated[str | None, Header(alias="Origin")] = None,
) -> AdminPrincipal:
    _require_origin(origin, settings)
    try:
        return authenticate_admin(engine, session_token)
    except AdminAuthError as exc:
        raise _auth_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc


AdminPrincipalDep = Annotated[AdminPrincipal, Depends(_admin_principal)]


def _operator_principal(principal: AdminPrincipalDep) -> AdminPrincipal:
    try:
        require_takeover_role(principal)
    except AdminAuthError as exc:
        raise _auth_error(exc) from exc
    return principal


OperatorPrincipalDep = Annotated[AdminPrincipal, Depends(_operator_principal)]


def _csrf_principal(
    principal: AdminPrincipalDep,
    csrf_cookie: Annotated[str | None, Cookie(alias=ADMIN_CSRF_COOKIE)] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> AdminPrincipal:
    try:
        verify_csrf(principal, csrf_cookie, csrf_header)
    except AdminAuthError as exc:
        raise _auth_error(exc) from exc
    return principal


CsrfPrincipalDep = Annotated[AdminPrincipal, Depends(_csrf_principal)]


def _operator_csrf_principal(principal: CsrfPrincipalDep) -> AdminPrincipal:
    try:
        require_takeover_role(principal)
    except AdminAuthError as exc:
        raise _auth_error(exc) from exc
    return principal


OperatorCsrfPrincipalDep = Annotated[AdminPrincipal, Depends(_operator_csrf_principal)]


def _identity(principal: AdminPrincipal) -> AdminIdentityResponse:
    return AdminIdentityResponse(
        user_id=principal.user_id,
        email=principal.email,
        display_name=principal.display_name,
        roles=tuple(sorted(principal.roles)),
        expires_at=principal.expires_at,
    )


def _set_auth_cookies(
    response: Response,
    settings: Settings,
    *,
    session_token: str,
    csrf_token: str,
    expires_at: datetime,
) -> None:
    secure = settings.app_env == "production"
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        session_token,
        max_age=settings.admin_session_ttl_seconds,
        expires=expires_at,
        path="/api/v1/admin",
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        ADMIN_CSRF_COOKIE,
        csrf_token,
        max_age=settings.admin_session_ttl_seconds,
        expires=expires_at,
        path="/api/v1/admin",
        secure=secure,
        httponly=False,
        samesite="lax",
    )


@auth_router.post(
    "/login",
    responses={401: ERROR_RESPONSE, 403: ERROR_RESPONSE, 422: ERROR_RESPONSE, 503: ERROR_RESPONSE},
)
def login(
    payload: LoginRequest,
    response: Response,
    engine: EngineDep,
    settings: SettingsDep,
    origin: Annotated[str | None, Header(alias="Origin")] = None,
) -> AdminIdentityResponse:
    _require_origin(origin, settings)
    try:
        result = login_admin(
            engine,
            settings,
            email=payload.email,
            password=payload.password.get_secret_value(),
        )
    except AdminAuthError as exc:
        raise _auth_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    _set_auth_cookies(
        response,
        settings,
        session_token=result.session_token,
        csrf_token=result.csrf_token,
        expires_at=result.principal.expires_at,
    )
    return _identity(result.principal)


@auth_router.get(
    "/me",
    responses={401: ERROR_RESPONSE, 403: ERROR_RESPONSE, 503: ERROR_RESPONSE},
)
def me(principal: AdminPrincipalDep) -> AdminIdentityResponse:
    return _identity(principal)


@auth_router.post(
    "/logout",
    status_code=204,
    responses={401: ERROR_RESPONSE, 403: ERROR_RESPONSE, 503: ERROR_RESPONSE},
)
def logout(
    response: Response,
    principal: CsrfPrincipalDep,
    engine: EngineDep,
) -> None:
    try:
        revoke_admin_session(engine, principal)
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/api/v1/admin")
    response.delete_cookie(ADMIN_CSRF_COOKIE, path="/api/v1/admin")


@admin_router.get(
    "/handoffs",
    responses={401: ERROR_RESPONSE, 403: ERROR_RESPONSE, 503: ERROR_RESPONSE},
)
def handoff_queue(
    principal: OperatorPrincipalDep,
    engine: EngineDep,
    state: Annotated[
        Literal["HUMAN_REQUESTED", "HUMAN_ASSIGNED", "HUMAN_ACTIVE"] | None,
        Query(),
    ] = None,
    assignment: Annotated[Literal["all", "unassigned", "me"], Query()] = "all",
    reason: Annotated[str | None, Query(max_length=50)] = None,
    priority: Annotated[
        Literal["LOW", "NORMAL", "HIGH", "URGENT"] | None,
        Query(),
    ] = None,
    waiting_at_least_seconds: Annotated[int | None, Query(ge=0, le=604_800)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> HandoffQueueResponse:
    try:
        result: HandoffQueue = list_handoffs(
            engine,
            principal,
            state=state,
            assignment=assignment,
            reason=reason,
            priority=priority,
            waiting_at_least_seconds=waiting_at_least_seconds,
            offset=offset,
            limit=limit,
        )
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return HandoffQueueResponse(
        items=tuple(HandoffQueueItemResponse.model_validate(item) for item in result.items),
        total=result.total,
        offset=offset,
        limit=limit,
    )


@admin_router.get(
    "/conversations/{conversation_id}",
    responses={401: ERROR_RESPONSE, 403: ERROR_RESPONSE, 404: ERROR_RESPONSE, 503: ERROR_RESPONSE},
)
def conversation_detail(
    conversation_id: Annotated[UUID, Path()],
    _principal: OperatorPrincipalDep,
    engine: EngineDep,
) -> AdminConversationResponse:
    try:
        result: AdminConversation = get_admin_conversation(engine, conversation_id)
    except ConversationError as exc:
        raise _conversation_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return AdminConversationResponse.model_validate(result)


@admin_router.post(
    "/conversations/{conversation_id}/claim",
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
def claim(
    request: Request,
    conversation_id: Annotated[UUID, Path()],
    principal: OperatorCsrfPrincipalDep,
    engine: EngineDep,
) -> HandoffStateResponse:
    try:
        result = claim_handoff(
            engine,
            principal,
            conversation_id,
            request_id=_request_id(request),
        )
    except ConversationError as exc:
        raise _conversation_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return HandoffStateResponse.model_validate(result)


@admin_router.post(
    "/conversations/{conversation_id}/messages",
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
def create_message(
    request: Request,
    conversation_id: Annotated[UUID, Path()],
    payload: AdminMessageRequest,
    principal: OperatorCsrfPrincipalDep,
    engine: EngineDep,
) -> AdminMessageResponse:
    try:
        result: AdminMessageRecord = add_admin_message(
            engine,
            principal,
            conversation_id,
            client_message_id=payload.client_message_id,
            content=payload.content,
            visibility=payload.visibility,
            request_id=_request_id(request),
        )
    except ConversationError as exc:
        raise _conversation_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return AdminMessageResponse.model_validate(result)


def _transition(
    request: Request,
    conversation_id: UUID,
    principal: AdminPrincipal,
    engine: Engine,
    target: Literal["RETURNED_TO_AI", "CLOSED"],
) -> HandoffStateResponse:
    try:
        result: HandoffState = transition_handoff(
            engine,
            principal,
            conversation_id,
            target=target,
            request_id=_request_id(request),
        )
    except ConversationError as exc:
        raise _conversation_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return HandoffStateResponse.model_validate(result)


@admin_router.post(
    "/conversations/{conversation_id}/return-to-ai",
    responses={401: ERROR_RESPONSE, 403: ERROR_RESPONSE, 404: ERROR_RESPONSE, 409: ERROR_RESPONSE},
)
def return_to_ai(
    request: Request,
    conversation_id: Annotated[UUID, Path()],
    principal: OperatorCsrfPrincipalDep,
    engine: EngineDep,
) -> HandoffStateResponse:
    return _transition(request, conversation_id, principal, engine, "RETURNED_TO_AI")


@admin_router.post(
    "/conversations/{conversation_id}/close",
    responses={401: ERROR_RESPONSE, 403: ERROR_RESPONSE, 404: ERROR_RESPONSE, 409: ERROR_RESPONSE},
)
def close(
    request: Request,
    conversation_id: Annotated[UUID, Path()],
    principal: OperatorCsrfPrincipalDep,
    engine: EngineDep,
) -> HandoffStateResponse:
    return _transition(request, conversation_id, principal, engine, "CLOSED")


@dataclass(frozen=True)
class AdminEventSubscription:
    principal: AdminPrincipal
    initial_events: tuple[AdminEventRecord, ...]
    initial_cursor: int


def _event_subscription(
    response: Response,
    principal: OperatorPrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID", ge=0)] = None,
) -> AdminEventSubscription:
    cursor = last_event_id or 0
    try:
        initial = load_admin_events(engine, after_id=cursor, limit=settings.sse_replay_limit)
    except ConversationError as exc:
        raise _conversation_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    return AdminEventSubscription(principal, initial, cursor)


AdminEventSubscriptionDep = Annotated[AdminEventSubscription, Depends(_event_subscription)]


def _server_event(event: AdminEventRecord) -> ServerSentEvent:
    return ServerSentEvent(
        id=str(event.event_id),
        event=event.event_type,
        retry=1_000,
        data={
            "event_id": event.event_id,
            "conversation_id": str(event.conversation_id),
            "timestamp": event.created_at.isoformat(),
            "visibility": event.visibility,
            "payload": event.payload,
        },
    )


async def admin_event_stream(
    request: Request,
    engine: Engine,
    subscription: AdminEventSubscription,
    settings: Settings,
) -> AsyncIterator[ServerSentEvent]:
    cursor = subscription.initial_cursor
    pending = subscription.initial_events
    keepalive_at = time.monotonic() + settings.sse_keepalive_seconds
    while _now() < subscription.principal.expires_at and not await request.is_disconnected():
        for event in pending:
            cursor = event.event_id
            yield _server_event(event)
        pending = ()
        if time.monotonic() >= keepalive_at:
            yield ServerSentEvent(event="keepalive", data={"timestamp": _now().isoformat()})
            keepalive_at = time.monotonic() + settings.sse_keepalive_seconds
        await asyncio.sleep(settings.sse_poll_interval_seconds)
        try:
            pending = await asyncio.to_thread(
                load_admin_events,
                engine,
                after_id=cursor,
                limit=settings.sse_replay_limit,
            )
        except (ConversationError, SQLAlchemyError):
            yield ServerSentEvent(event="error", data={"code": "EVENT_STREAM_UNAVAILABLE"})
            return


@admin_router.get(
    "/events",
    response_class=EventSourceResponse,
    responses={401: ERROR_RESPONSE, 403: ERROR_RESPONSE, 409: ERROR_RESPONSE, 503: ERROR_RESPONSE},
)
async def events(
    request: Request,
    subscription: AdminEventSubscriptionDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> AsyncIterator[ServerSentEvent]:
    async for event in admin_event_stream(request, engine, subscription, settings):
        yield event


def _now() -> datetime:
    return datetime.now(UTC)
