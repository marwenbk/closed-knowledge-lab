from __future__ import annotations

import asyncio
import time
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, Path, Query, Request, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.admin_auth import (
    ADMIN_CSRF_COOKIE,
    ADMIN_SESSION_COOKIE,
    AUDIT_ROLES,
    FEEDBACK_ROLES,
    KNOWLEDGE_EDITOR_ROLES,
    KNOWLEDGE_PUBLISHER_ROLES,
    KNOWLEDGE_ROLES,
    REVIEW_ROLES,
    TUNING_EDITOR_ROLES,
    TUNING_PUBLISHER_ROLES,
    TUNING_ROLES,
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
from app.answering import AnsweringError
from app.api import (
    ApiError,
    EngineDep,
    ErrorResponse,
    SettingsDep,
    embedding_provider,
    llm_provider,
)
from app.config import Settings
from app.conversations import ConversationError, normalize_origin
from app.embeddings import EmbeddingError
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
from app.llm import LLMError
from app.oversight import (
    FeedbackCategory,
    create_feedback,
    list_audit_events,
    list_feedback,
)
from app.retrieval import RetrievalError
from app.reviews import ReviewAction, review_message
from app.tuning import (
    PromptBundle,
    RetrievalTuning,
    TuningError,
    activate_runtime_version,
    create_prompt_draft,
    create_settings_draft,
    evaluate_prompt_version,
    evaluate_settings_version,
    get_evaluation,
    get_prompt_version,
    get_settings_version,
    list_evaluations,
    list_prompt_versions,
    list_settings_versions,
    update_prompt_version,
    update_settings_version,
)

ERROR_RESPONSE: dict[str, Any] = {"model": ErrorResponse}
ADMIN_READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: ERROR_RESPONSE,
    403: ERROR_RESPONSE,
    503: ERROR_RESPONSE,
}
ADMIN_DETAIL_RESPONSES: dict[int | str, dict[str, Any]] = {
    **ADMIN_READ_RESPONSES,
    404: ERROR_RESPONSE,
}
ADMIN_MUTATION_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: ERROR_RESPONSE,
    403: ERROR_RESPONSE,
    404: ERROR_RESPONSE,
    409: ERROR_RESPONSE,
    422: ERROR_RESPONSE,
    503: ERROR_RESPONSE,
}
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
    review_status: str
    review_regeneration_count: int
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


class DashboardPendingReviewResponse(BaseModel):
    conversation_id: UUID
    message_id: UUID
    content: str
    status: Literal["PENDING", "REGENERATING"]
    created_at: datetime


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
    pending_reviews: tuple[DashboardPendingReviewResponse, ...]
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


class EvaluationSummaryResponse(BaseModel):
    id: UUID
    suite: str
    mode: str
    status: str
    baseline_run_id: UUID | None
    metrics: dict[str, object]
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None


class PromptVersionResponse(BaseModel):
    id: UUID
    version: str
    status: str
    content_checksum: str
    source_version_id: UUID | None
    created_by: UUID | None
    activated_by: UUID | None
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None
    prompts: PromptBundle
    evaluation: EvaluationSummaryResponse | None


class PromptVersionListResponse(BaseModel):
    items: tuple[PromptVersionResponse, ...]


class SettingsVersionResponse(BaseModel):
    id: UUID
    version: str
    status: str
    content_checksum: str
    requires_reindex: bool
    source_version_id: UUID | None
    created_by: UUID | None
    activated_by: UUID | None
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None
    settings: RetrievalTuning
    evaluation: EvaluationSummaryResponse | None


class SettingsVersionListResponse(BaseModel):
    items: tuple[SettingsVersionResponse, ...]


class CreateRuntimeDraftRequest(StrictRequest):
    version: str = Field(
        min_length=5,
        max_length=50,
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$",
    )


class PromptActionRequest(StrictRequest):
    action: Literal["EVALUATE", "ACTIVATE", "ROLLBACK"]
    confirm_live_cost: bool = False


class SettingsActionRequest(StrictRequest):
    action: Literal["EVALUATE", "ACTIVATE", "ROLLBACK"]


class EvaluationDetailResponse(EvaluationSummaryResponse):
    kb_version_id: UUID
    kb_manifest_checksum: str
    prompt_version: str
    prompt_checksum: str
    settings_version: str
    settings_checksum: str
    model_name: str
    embedding_model: str
    embedding_version: str
    started_by: UUID


class EvaluationListResponse(BaseModel):
    items: tuple[EvaluationSummaryResponse, ...]
    total: int
    offset: int
    limit: int


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


class ReviewMessageRequest(StrictRequest):
    action: ReviewAction
    content: str | None = Field(default=None, max_length=4000)
    note: str | None = Field(default=None, max_length=1000)


class FeedbackRequest(StrictRequest):
    rag_run_id: UUID = Field(strict=False)
    category: FeedbackCategory
    note: str | None = Field(default=None, max_length=2000)


class FeedbackResponse(BaseModel):
    id: UUID
    rag_run_id: UUID
    conversation_id: UUID
    category: str
    note: str | None
    created_by: UUID
    created_by_name: str | None
    created_at: datetime


class FeedbackListResponse(BaseModel):
    items: tuple[FeedbackResponse, ...]
    total: int
    offset: int
    limit: int


class AuditEventResponse(BaseModel):
    id: UUID
    event_type: str
    actor_type: str
    actor_id: str | None
    resource_type: str
    resource_id: str
    request_id: UUID | None
    before: dict[str, object] | None
    after: dict[str, object] | None
    metadata: dict[str, object]
    created_at: datetime


class AuditEventListResponse(BaseModel):
    items: tuple[AuditEventResponse, ...]
    total: int
    offset: int
    limit: int


def _request_id(request: Request) -> UUID:
    return UUID(str(request.state.request_id))


def _auth_error(exc: AdminAuthError) -> ApiError:
    return ApiError(exc.status_code, exc.code, str(exc))


def _conversation_error(exc: ConversationError) -> ApiError:
    return ApiError(exc.status_code, exc.code, str(exc))


def _knowledge_error(exc: KnowledgeWorkflowError) -> ApiError:
    return ApiError(exc.status_code, exc.code, str(exc))


def _tuning_error(exc: TuningError) -> ApiError:
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


def _tuning_principal(principal: AdminPrincipalDep) -> AdminPrincipal:
    try:
        require_any_role(principal, TUNING_ROLES)
    except AdminAuthError as exc:
        raise _auth_error(exc) from exc
    return principal


def _tuning_editor_principal(principal: CsrfPrincipalDep) -> AdminPrincipal:
    try:
        require_any_role(principal, TUNING_EDITOR_ROLES)
    except AdminAuthError as exc:
        raise _auth_error(exc) from exc
    return principal


TuningPrincipalDep = Annotated[AdminPrincipal, Depends(_tuning_principal)]
TuningEditorPrincipalDep = Annotated[AdminPrincipal, Depends(_tuning_editor_principal)]


def _audit_principal(principal: AdminPrincipalDep) -> AdminPrincipal:
    try:
        require_any_role(principal, AUDIT_ROLES)
    except AdminAuthError as exc:
        raise _auth_error(exc) from exc
    return principal


def _feedback_principal(principal: CsrfPrincipalDep) -> AdminPrincipal:
    try:
        require_any_role(principal, FEEDBACK_ROLES)
    except AdminAuthError as exc:
        raise _auth_error(exc) from exc
    return principal


AuditPrincipalDep = Annotated[AdminPrincipal, Depends(_audit_principal)]
FeedbackPrincipalDep = Annotated[AdminPrincipal, Depends(_feedback_principal)]


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


@admin_router.get("/prompts", responses=ADMIN_READ_RESPONSES)
def prompt_versions(
    _principal: TuningPrincipalDep,
    engine: EngineDep,
) -> PromptVersionListResponse:
    try:
        items = list_prompt_versions(engine)
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return PromptVersionListResponse(
        items=tuple(PromptVersionResponse.model_validate(item) for item in items)
    )


@admin_router.post(
    "/prompts",
    status_code=201,
    responses=ADMIN_MUTATION_RESPONSES,
)
def create_prompt_version(
    request: Request,
    payload: CreateRuntimeDraftRequest,
    principal: TuningEditorPrincipalDep,
    engine: EngineDep,
) -> PromptVersionResponse:
    try:
        result = create_prompt_draft(
            engine,
            version=payload.version,
            actor_id=principal.user_id,
            request_id=_request_id(request),
        )
    except TuningError as exc:
        raise _tuning_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return PromptVersionResponse.model_validate(result)


@admin_router.get(
    "/prompts/{version_id}",
    responses=ADMIN_DETAIL_RESPONSES,
)
def prompt_version(
    version_id: Annotated[UUID, Path()],
    _principal: TuningPrincipalDep,
    engine: EngineDep,
) -> PromptVersionResponse:
    try:
        return PromptVersionResponse.model_validate(get_prompt_version(engine, version_id))
    except TuningError as exc:
        raise _tuning_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc


@admin_router.put(
    "/prompts/{version_id}",
    responses=ADMIN_MUTATION_RESPONSES,
)
def revise_prompt_version(
    request: Request,
    version_id: Annotated[UUID, Path()],
    payload: PromptBundle,
    principal: TuningEditorPrincipalDep,
    engine: EngineDep,
) -> PromptVersionResponse:
    try:
        result = update_prompt_version(
            engine,
            version_id,
            payload,
            actor_id=principal.user_id,
            request_id=_request_id(request),
        )
    except TuningError as exc:
        raise _tuning_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return PromptVersionResponse.model_validate(result)


@admin_router.post(
    "/prompts/{version_id}/actions",
    responses=ADMIN_MUTATION_RESPONSES,
)
def run_prompt_action(
    request: Request,
    version_id: Annotated[UUID, Path()],
    payload: PromptActionRequest,
    principal: CsrfPrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> PromptVersionResponse:
    try:
        require_any_role(principal, TUNING_PUBLISHER_ROLES)
        if payload.action == "EVALUATE":
            if payload.confirm_live_cost is not True:
                raise TuningError(
                    422,
                    "LIVE_COST_CONFIRMATION_REQUIRED",
                    "Full DeepSeek evaluation requires explicit cost confirmation",
                )
            result = evaluate_prompt_version(
                engine,
                embedding_provider(request),
                llm_provider(request),
                settings,
                version_id,
                actor_id=principal.user_id,
                request_id=_request_id(request),
            )
        else:
            result = activate_runtime_version(
                engine,
                settings,
                version_id,
                kind="PROMPT",
                rollback=payload.action == "ROLLBACK",
                actor_id=principal.user_id,
                request_id=_request_id(request),
            )
    except AdminAuthError as exc:
        raise _auth_error(exc) from exc
    except TuningError as exc:
        raise _tuning_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return PromptVersionResponse.model_validate(result)


@admin_router.get("/settings", responses=ADMIN_READ_RESPONSES)
def settings_versions(
    _principal: TuningPrincipalDep,
    engine: EngineDep,
) -> SettingsVersionListResponse:
    try:
        items = list_settings_versions(engine)
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return SettingsVersionListResponse(
        items=tuple(SettingsVersionResponse.model_validate(item) for item in items)
    )


@admin_router.post(
    "/settings",
    status_code=201,
    responses=ADMIN_MUTATION_RESPONSES,
)
def create_settings_version(
    request: Request,
    payload: CreateRuntimeDraftRequest,
    principal: TuningEditorPrincipalDep,
    engine: EngineDep,
) -> SettingsVersionResponse:
    try:
        result = create_settings_draft(
            engine,
            version=payload.version,
            actor_id=principal.user_id,
            request_id=_request_id(request),
        )
    except TuningError as exc:
        raise _tuning_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return SettingsVersionResponse.model_validate(result)


@admin_router.get(
    "/settings/{version_id}",
    responses=ADMIN_DETAIL_RESPONSES,
)
def settings_version(
    version_id: Annotated[UUID, Path()],
    _principal: TuningPrincipalDep,
    engine: EngineDep,
) -> SettingsVersionResponse:
    try:
        return SettingsVersionResponse.model_validate(get_settings_version(engine, version_id))
    except TuningError as exc:
        raise _tuning_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc


@admin_router.put(
    "/settings/{version_id}",
    responses=ADMIN_MUTATION_RESPONSES,
)
def revise_settings_version(
    request: Request,
    version_id: Annotated[UUID, Path()],
    payload: RetrievalTuning,
    principal: TuningEditorPrincipalDep,
    engine: EngineDep,
) -> SettingsVersionResponse:
    try:
        result = update_settings_version(
            engine,
            version_id,
            payload,
            actor_id=principal.user_id,
            request_id=_request_id(request),
        )
    except TuningError as exc:
        raise _tuning_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return SettingsVersionResponse.model_validate(result)


@admin_router.post(
    "/settings/{version_id}/actions",
    responses=ADMIN_MUTATION_RESPONSES,
)
def run_settings_action(
    request: Request,
    version_id: Annotated[UUID, Path()],
    payload: SettingsActionRequest,
    principal: CsrfPrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> SettingsVersionResponse:
    try:
        require_any_role(
            principal,
            TUNING_EDITOR_ROLES if payload.action == "EVALUATE" else TUNING_PUBLISHER_ROLES,
        )
        if payload.action == "EVALUATE":
            result = evaluate_settings_version(
                engine,
                embedding_provider(request),
                settings,
                version_id,
                actor_id=principal.user_id,
                request_id=_request_id(request),
            )
        else:
            result = activate_runtime_version(
                engine,
                settings,
                version_id,
                kind="SETTINGS",
                rollback=payload.action == "ROLLBACK",
                actor_id=principal.user_id,
                request_id=_request_id(request),
            )
    except AdminAuthError as exc:
        raise _auth_error(exc) from exc
    except TuningError as exc:
        raise _tuning_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return SettingsVersionResponse.model_validate(result)


@admin_router.get(
    "/evaluations",
    responses={401: ERROR_RESPONSE, 403: ERROR_RESPONSE, 503: ERROR_RESPONSE},
)
def evaluations(
    _principal: TuningPrincipalDep,
    engine: EngineDep,
    status: Annotated[Literal["RUNNING", "PASSED", "FAILED"] | None, Query()] = None,
    mode: Annotated[Literal["RETRIEVAL", "FULL"] | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> EvaluationListResponse:
    try:
        items, total = list_evaluations(
            engine, status=status, mode=mode, offset=offset, limit=limit
        )
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return EvaluationListResponse(
        items=tuple(EvaluationSummaryResponse.model_validate(item) for item in items),
        total=total,
        offset=offset,
        limit=limit,
    )


@admin_router.get(
    "/evaluations/{run_id}",
    responses=ADMIN_DETAIL_RESPONSES,
)
def evaluation(
    run_id: Annotated[UUID, Path()],
    _principal: TuningPrincipalDep,
    engine: EngineDep,
) -> EvaluationDetailResponse:
    try:
        return EvaluationDetailResponse.model_validate(get_evaluation(engine, run_id))
    except TuningError as exc:
        raise _tuning_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc


@admin_router.post(
    "/messages/{message_id}/review",
    responses=ADMIN_MUTATION_RESPONSES,
)
def review_pending_message(
    request: Request,
    message_id: Annotated[UUID, Path()],
    payload: ReviewMessageRequest,
    principal: CsrfPrincipalDep,
    engine: EngineDep,
    settings: SettingsDep,
) -> AdminConversationResponse:
    try:
        require_any_role(principal, REVIEW_ROLES)
        needs_llm = payload.action in {"EDIT_AND_SEND", "REJECT_AND_REGENERATE"}
        conversation_id = review_message(
            engine,
            embedding_provider(request) if payload.action == "REJECT_AND_REGENERATE" else None,
            llm_provider(request) if needs_llm else None,
            settings,
            principal,
            message_id,
            action=payload.action,
            content=payload.content,
            note=payload.note,
            request_id=_request_id(request),
        )
        result = get_admin_conversation(engine, conversation_id)
    except AdminAuthError as exc:
        raise _auth_error(exc) from exc
    except ConversationError as exc:
        raise _conversation_error(exc) from exc
    except (AnsweringError, EmbeddingError, LLMError, RetrievalError) as exc:
        raise ApiError(503, "REVIEW_EXECUTION_FAILED", "The review action failed closed") from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return AdminConversationResponse.model_validate(result)


@admin_router.post(
    "/feedback",
    status_code=201,
    responses=ADMIN_MUTATION_RESPONSES,
)
def add_feedback(
    request: Request,
    payload: FeedbackRequest,
    principal: FeedbackPrincipalDep,
    engine: EngineDep,
) -> FeedbackResponse:
    try:
        result = create_feedback(
            engine,
            rag_run_id=payload.rag_run_id,
            category=payload.category,
            note=payload.note,
            actor_id=principal.user_id,
            request_id=_request_id(request),
        )
    except ConversationError as exc:
        raise _conversation_error(exc) from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return FeedbackResponse.model_validate(result)


@admin_router.get("/feedback", responses=ADMIN_READ_RESPONSES)
def feedback_history(
    _principal: AuditPrincipalDep,
    engine: EngineDep,
    rag_run_id: Annotated[UUID | None, Query()] = None,
    category: Annotated[FeedbackCategory | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> FeedbackListResponse:
    try:
        items, total = list_feedback(
            engine,
            rag_run_id=rag_run_id,
            category=category,
            offset=offset,
            limit=limit,
        )
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return FeedbackListResponse(
        items=tuple(FeedbackResponse.model_validate(item) for item in items),
        total=total,
        offset=offset,
        limit=limit,
    )


@admin_router.get("/audit-events", responses=ADMIN_READ_RESPONSES)
def audit_events(
    _principal: AuditPrincipalDep,
    engine: EngineDep,
    event_type: Annotated[str | None, Query(max_length=100)] = None,
    actor_type: Annotated[str | None, Query(max_length=50)] = None,
    resource_type: Annotated[str | None, Query(max_length=100)] = None,
    resource_id: Annotated[str | None, Query(max_length=200)] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AuditEventListResponse:
    try:
        items, total = list_audit_events(
            engine,
            event_type=event_type,
            actor_type=actor_type,
            resource_type=resource_type,
            resource_id=resource_id,
            created_from=created_from,
            created_to=created_to,
            offset=offset,
            limit=limit,
        )
    except SQLAlchemyError as exc:
        raise ApiError(503, "DATABASE_NOT_READY", "The database is unavailable") from exc
    return AuditEventListResponse(
        items=tuple(AuditEventResponse.model_validate(item) for item in items),
        total=total,
        offset=offset,
        limit=limit,
    )


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
