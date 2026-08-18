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
    KNOWLEDGE_EDITOR_ROLES,
    KNOWLEDGE_PUBLISHER_ROLES,
    KNOWLEDGE_ROLES,
    AdminAuthError,
    AdminPrincipal,
    authenticate_admin,
    login_admin,
    require_any_role,
    require_takeover_role,
    revoke_admin_session,
    verify_csrf,
)
from app.admin_views import (
    dashboard_snapshot,
    get_rag_run,
)
from app.api import ApiError, EngineDep, ErrorResponse, SettingsDep, embedding_provider
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
    latest_admin_event_id,
    list_handoffs,
    load_admin_events,
    transition_handoff,
)
from app.knowledge_workflow import (
    KnowledgeWorkflowError,
    create_draft,
    evaluate_version,
    index_version,
    list_versions,
    publish_version,
    validate_version,
)
from app.knowledge_workflow import (
    get_document as get_workflow_document,
)
from app.knowledge_workflow import (
    get_version as get_workflow_version,
)
from app.knowledge_workflow import (
    update_document as update_workflow_document,
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


class DashboardKnowledgeResponse(BaseModel):
    dataset_id: str
    dataset_version: str
    status: str
    document_count: int
    chunk_count: int


class DashboardRuntimeResponse(BaseModel):
    model: str
    model_version: str | None
    prompt_version: str
    settings_version: str
    embedding_version: str


class DashboardConversationResponse(BaseModel):
    open: int
    waiting: int
    assigned: int
    human_active: int
    oldest_waiting_seconds: int


class DashboardQualityResponse(BaseModel):
    answerability: dict[str, int]
    refusal_rate_percent: float
    conflict_rate_percent: float
    grounding_failure_rate_percent: float
    average_ai_latency_ms: float
    average_handoff_wait_seconds: float


class LatestEvaluationResponse(BaseModel):
    mode: str | None
    passed: bool | None
    dataset_version: str | None
    evaluated_cases: int | None
    updated_at: datetime


class DashboardResponse(BaseModel):
    generated_at: datetime
    knowledge: DashboardKnowledgeResponse
    runtime: DashboardRuntimeResponse
    conversations: DashboardConversationResponse
    quality: DashboardQualityResponse
    latest_evaluation: LatestEvaluationResponse | None


class KnowledgeChunkResponse(BaseModel):
    id: UUID
    stable_chunk_key: str
    section: str
    section_path: tuple[str, ...]
    ordinal: int
    content: str
    token_count: int


class KnowledgeValidationIssueResponse(BaseModel):
    code: str
    document_key: str
    message: str


class KnowledgeValidationResponse(BaseModel):
    passed: bool
    document_count: int
    chunk_count: int
    errors: tuple[KnowledgeValidationIssueResponse, ...]
    duplicate_policy_ids: dict[str, list[str]]
    conflict_fixture_ids: tuple[str, ...]


class KnowledgeEvaluationResponse(BaseModel):
    id: UUID
    status: str
    suite: str
    manifest_checksum: str
    metrics: dict[str, object]
    started_at: datetime
    completed_at: datetime | None


class KnowledgeVersionResponse(BaseModel):
    id: UUID
    dataset_id: str
    dataset_version: str
    status: str
    source_version_id: UUID | None
    manifest_checksum: str
    document_count: int
    chunk_count: int
    embedded_chunk_count: int
    validation: KnowledgeValidationResponse | None
    validated_at: datetime | None
    evaluation: KnowledgeEvaluationResponse | None
    created_by: UUID | None
    activated_by: UUID | None
    created_at: datetime
    activated_at: datetime | None


class KnowledgeVersionListResponse(BaseModel):
    items: tuple[KnowledgeVersionResponse, ...]


class KnowledgeVersionDocumentResponse(BaseModel):
    id: UUID
    document_key: str
    title: str
    source_path: str
    checksum: str
    chunk_count: int
    sort_order: int


class KnowledgeVersionDetailResponse(KnowledgeVersionResponse):
    documents: tuple[KnowledgeVersionDocumentResponse, ...]


class KnowledgeDependenciesResponse(BaseModel):
    fact_ids: tuple[str, ...]
    evaluation_case_ids: tuple[str, ...]


class KnowledgeRevisionHistoryResponse(BaseModel):
    revision_number: int
    content_checksum: str
    created_at: datetime


class KnowledgeWorkflowDocumentResponse(BaseModel):
    id: UUID
    version_id: UUID
    dataset_version: str
    version_status: str
    document_key: str
    title: str
    source_path: str
    checksum: str
    revision_number: int
    content_markdown: str
    front_matter: dict[str, object]
    revisions: tuple[KnowledgeRevisionHistoryResponse, ...]
    dependencies: KnowledgeDependenciesResponse
    chunks: tuple[KnowledgeChunkResponse, ...]


class CreateKnowledgeDraftRequest(StrictRequest):
    dataset_version: str = Field(
        min_length=5,
        max_length=50,
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$",
    )


class UpdateKnowledgeDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    content_markdown: str = Field(min_length=1, max_length=250_000)


class KnowledgeActionRequest(StrictRequest):
    action: Literal["VALIDATE", "INDEX", "EVALUATE", "ACTIVATE", "ROLLBACK"]


class RagModelResponse(BaseModel):
    provider: str
    name: str
    version: str | None
    prompt_version: str
    settings_version: str
    embedding_version: str


class RagKnowledgeResponse(BaseModel):
    dataset_id: str | None
    dataset_version: str | None


class RagRunDetailResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID | None
    original_query: str
    conversation_context: tuple[str, ...]
    retrieval_query: str
    status: str
    answerability_status: str | None
    verification_status: str | None
    error_code: str | None
    model: RagModelResponse
    knowledge: RagKnowledgeResponse
    user_message: str | None
    assistant_message: str | None
    citations: tuple[dict[str, object], ...]
    trace: dict[str, object] | None
    regenerated: bool | None
    latency_ms: float | None
    created_at: datetime
    completed_at: datetime | None


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


def _knowledge_error(exc: KnowledgeWorkflowError) -> ApiError:
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
    session_token: Annotated[str | None, Cookie(alias=ADMIN_SESSION_COOKIE)] = None,
) -> AdminPrincipal:
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
    settings: SettingsDep,
    csrf_cookie: Annotated[str | None, Cookie(alias=ADMIN_CSRF_COOKIE)] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    origin: Annotated[str | None, Header(alias="Origin")] = None,
) -> AdminPrincipal:
    _require_origin(origin, settings)
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


def _knowledge_principal(principal: AdminPrincipalDep) -> AdminPrincipal:
    try:
        require_any_role(principal, KNOWLEDGE_ROLES)
    except AdminAuthError as exc:
        raise _auth_error(exc) from exc
    return principal


def _knowledge_editor_principal(principal: CsrfPrincipalDep) -> AdminPrincipal:
    try:
        require_any_role(principal, KNOWLEDGE_EDITOR_ROLES)
    except AdminAuthError as exc:
        raise _auth_error(exc) from exc
    return principal


KnowledgePrincipalDep = Annotated[AdminPrincipal, Depends(_knowledge_principal)]
KnowledgeEditorPrincipalDep = Annotated[AdminPrincipal, Depends(_knowledge_editor_principal)]


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
        # The admin application lives at /admin and must be able to read this
        # double-submit token. The opaque session cookie remains API-scoped.
        path="/",
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
    response.delete_cookie(ADMIN_CSRF_COOKIE, path="/")


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
    "/dashboard",
    responses={401: ERROR_RESPONSE, 403: ERROR_RESPONSE, 503: ERROR_RESPONSE},
)
def dashboard(
    _principal: AdminPrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> DashboardResponse:
    try:
        result = dashboard_snapshot(engine, settings)
    except ConversationError as exc:
        raise _conversation_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return DashboardResponse.model_validate(result)


@admin_router.get(
    "/knowledge/versions",
    responses={401: ERROR_RESPONSE, 403: ERROR_RESPONSE, 503: ERROR_RESPONSE},
)
def knowledge_versions(
    _principal: KnowledgePrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> KnowledgeVersionListResponse:
    try:
        items = list_versions(engine, dataset_id=settings.expected_dataset_id)
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return KnowledgeVersionListResponse(
        items=tuple(KnowledgeVersionResponse.model_validate(item) for item in items)
    )


@admin_router.post(
    "/knowledge/versions",
    status_code=201,
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
def create_knowledge_version(
    request: Request,
    payload: CreateKnowledgeDraftRequest,
    principal: KnowledgeEditorPrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> KnowledgeVersionResponse:
    try:
        result = create_draft(
            engine,
            dataset_id=settings.expected_dataset_id,
            dataset_version=payload.dataset_version,
            actor_id=principal.user_id,
            request_id=_request_id(request),
        )
    except KnowledgeWorkflowError as exc:
        raise _knowledge_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return KnowledgeVersionResponse.model_validate(result)


@admin_router.get(
    "/knowledge/versions/{version_id}",
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
def knowledge_version(
    version_id: Annotated[UUID, Path()],
    _principal: KnowledgePrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> KnowledgeVersionDetailResponse:
    try:
        result = get_workflow_version(
            engine,
            dataset_id=settings.expected_dataset_id,
            version_id=version_id,
        )
    except KnowledgeWorkflowError as exc:
        raise _knowledge_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return KnowledgeVersionDetailResponse.model_validate(result)


@admin_router.get(
    "/knowledge/versions/{version_id}/documents/{document_id}",
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
def knowledge_version_document(
    version_id: Annotated[UUID, Path()],
    document_id: Annotated[UUID, Path()],
    _principal: KnowledgePrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> KnowledgeWorkflowDocumentResponse:
    try:
        result = get_workflow_document(
            engine,
            dataset_id=settings.expected_dataset_id,
            version_id=version_id,
            document_id=document_id,
        )
    except KnowledgeWorkflowError as exc:
        raise _knowledge_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return KnowledgeWorkflowDocumentResponse.model_validate(result)


@admin_router.put(
    "/knowledge/versions/{version_id}/documents/{document_id}",
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
def revise_knowledge_document(
    request: Request,
    version_id: Annotated[UUID, Path()],
    document_id: Annotated[UUID, Path()],
    payload: UpdateKnowledgeDocumentRequest,
    principal: KnowledgeEditorPrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> KnowledgeWorkflowDocumentResponse:
    try:
        result = update_workflow_document(
            engine,
            dataset_id=settings.expected_dataset_id,
            version_id=version_id,
            document_id=document_id,
            content=payload.content_markdown,
            actor_id=principal.user_id,
            request_id=_request_id(request),
        )
    except KnowledgeWorkflowError as exc:
        raise _knowledge_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return KnowledgeWorkflowDocumentResponse.model_validate(result)


@admin_router.post(
    "/knowledge/versions/{version_id}/actions",
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
def run_knowledge_action(
    request: Request,
    version_id: Annotated[UUID, Path()],
    payload: KnowledgeActionRequest,
    principal: CsrfPrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> KnowledgeVersionDetailResponse:
    publishing = payload.action in {"ACTIVATE", "ROLLBACK"}
    try:
        require_any_role(
            principal,
            KNOWLEDGE_PUBLISHER_ROLES if publishing else KNOWLEDGE_EDITOR_ROLES,
        )
        request_id = _request_id(request)
        if payload.action == "VALIDATE":
            validate_version(
                engine,
                dataset_id=settings.expected_dataset_id,
                version_id=version_id,
                actor_id=principal.user_id,
                request_id=request_id,
            )
        elif payload.action in {"INDEX", "EVALUATE"}:
            provider = embedding_provider(request)
            if payload.action == "INDEX":
                index_version(
                    engine,
                    provider,
                    settings,
                    dataset_id=settings.expected_dataset_id,
                    version_id=version_id,
                    actor_id=principal.user_id,
                    request_id=request_id,
                )
            else:
                evaluate_version(
                    engine,
                    provider,
                    settings,
                    dataset_id=settings.expected_dataset_id,
                    version_id=version_id,
                    actor_id=principal.user_id,
                    request_id=request_id,
                )
        else:
            publish_version(
                engine,
                settings,
                dataset_id=settings.expected_dataset_id,
                version_id=version_id,
                actor_id=principal.user_id,
                request_id=request_id,
                rollback=payload.action == "ROLLBACK",
            )
        result = get_workflow_version(
            engine,
            dataset_id=settings.expected_dataset_id,
            version_id=version_id,
        )
    except AdminAuthError as exc:
        raise _auth_error(exc) from exc
    except KnowledgeWorkflowError as exc:
        raise _knowledge_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return KnowledgeVersionDetailResponse.model_validate(result)


@admin_router.get(
    "/rag-runs/{rag_run_id}",
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
def rag_run_detail(
    rag_run_id: Annotated[UUID, Path()],
    _principal: OperatorPrincipalDep,
    engine: EngineDep,
) -> RagRunDetailResponse:
    try:
        result = get_rag_run(engine, rag_run_id)
    except ConversationError as exc:
        raise _conversation_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return RagRunDetailResponse.model_validate(result)


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
    try:
        if last_event_id is None:
            # The queue/detail requests provide the initial snapshot. Replaying
            # the bounded tail closes the small snapshot-to-stream race.
            cursor = max(0, latest_admin_event_id(engine) - settings.sse_replay_limit)
            initial = load_admin_events(
                engine,
                after_id=cursor,
                limit=settings.sse_replay_limit,
            )
        else:
            cursor = last_event_id
            initial = load_admin_events(
                engine,
                after_id=cursor,
                limit=settings.sse_replay_limit,
            )
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
