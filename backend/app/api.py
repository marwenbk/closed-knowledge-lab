from __future__ import annotations

import threading
import unicodedata
from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.answering import AnsweringError, GroundedAnswer, answer_knowledge
from app.config import Settings
from app.embeddings import EmbeddingError, EmbeddingProvider, OnnxE5EmbeddingProvider
from app.llm import DeepSeekProvider, LLMError, LLMProvider
from app.retrieval import RetrievalError, retrieve_knowledge
from app.services import KnowledgeBaseUnavailable, kb_status, readiness


class ApiError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: UUID


class ErrorResponse(BaseModel):
    error: ErrorDetail


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=1000)

    @field_validator("query")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise ValueError("query must not contain control characters")
        return value


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, dict[str, Any]]


class EmbeddingStatus(BaseModel):
    status: str
    model_id: str | None
    model_version: str | None
    dimensions: int | None


class TokenRange(BaseModel):
    minimum: int
    maximum: int


class KnowledgeStatusResponse(BaseModel):
    dataset_id: str
    dataset_version: str
    generator_version: str
    language: str
    status: str
    manifest_checksum: str
    document_count: int
    chunk_count: int
    embedded_chunk_count: int
    embedding: EmbeddingStatus
    chunk_token_range: TokenRange
    activated_at: datetime | None


class RetrievalSignal(BaseModel):
    rank: int
    score: float


class RetrievalMatchResponse(BaseModel):
    chunk_id: UUID
    stable_chunk_key: str
    document_key: str
    document_title: str
    source_path: str
    section: str
    section_path: list[str]
    ordinal: int
    content: str
    rrf_score: float
    signals: dict[str, RetrievalSignal]


class SecondHop(BaseModel):
    used: bool
    query: str | None


class RetrievalResponse(BaseModel):
    query: str
    dataset_id: str
    dataset_version: str
    embedding_model: str
    embedding_version: str
    result_count: int
    trigram_fallback_used: bool
    second_hop: SecondHop
    duration_ms: float
    matches: list[RetrievalMatchResponse]


def _engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


EngineDep = Annotated[Engine, Depends(_engine)]
SettingsDep = Annotated[Settings, Depends(_settings)]


def _provider(request: Request) -> EmbeddingProvider:
    provider = cast(EmbeddingProvider | None, request.app.state.embedding_provider)
    if provider is None:
        lock = cast(threading.Lock, request.app.state.embedding_provider_lock)
        with lock:
            provider = cast(EmbeddingProvider | None, request.app.state.embedding_provider)
            if provider is None:
                try:
                    provider = OnnxE5EmbeddingProvider(request.app.state.settings)
                except EmbeddingError as exc:
                    raise ApiError(
                        503,
                        "RETRIEVAL_NOT_READY",
                        "Closed-knowledge retrieval is not ready",
                    ) from exc
                request.app.state.embedding_provider = provider
    return provider


def _llm_provider(request: Request) -> LLMProvider:
    provider = cast(LLMProvider | None, request.app.state.llm_provider)
    if provider is None:
        lock = cast(threading.Lock, request.app.state.llm_provider_lock)
        with lock:
            provider = cast(LLMProvider | None, request.app.state.llm_provider)
            if provider is None:
                provider = DeepSeekProvider(request.app.state.settings)
                request.app.state.llm_provider = provider
    return provider


ERROR_RESPONSE = {"model": ErrorResponse}
system_router = APIRouter(tags=["system"])
knowledge_router = APIRouter(prefix="/api/v1/kb", tags=["knowledge-base"])


@system_router.get("/health", responses={500: ERROR_RESPONSE})
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="topmed-api")


@system_router.get(
    "/ready",
    responses={503: {"model": ReadinessResponse}, 500: ERROR_RESPONSE},
)
def ready(
    request: Request,
    response: Response,
    engine: EngineDep,
    settings: SettingsDep,
) -> ReadinessResponse:
    try:
        _provider(request)
    except ApiError:
        runtime_ready = False
    else:
        runtime_ready = True
    try:
        llm = _llm_provider(request)
        llm_version = llm.ensure_ready()
    except LLMError:
        llm_runtime_ready = False
        llm_version = None
    else:
        llm_runtime_ready = True
    result = ReadinessResponse.model_validate(
        readiness(
            engine,
            expected_dataset_id=settings.expected_dataset_id,
            expected_embedding_model=settings.embedding_model_id,
            expected_embedding_version=settings.embedding_model_revision,
            expected_embedding_dimensions=settings.embedding_dimensions,
            embedding_runtime_ready=runtime_ready,
            llm_runtime_ready=llm_runtime_ready,
            expected_chat_model=settings.chat_model,
            chat_model_version=llm_version,
        )
    )
    response.status_code = 200 if result.status == "ready" else 503
    return result


@knowledge_router.get("/status", responses={503: ERROR_RESPONSE, 500: ERROR_RESPONSE})
def knowledge_status(engine: EngineDep, settings: SettingsDep) -> KnowledgeStatusResponse:
    try:
        result = kb_status(engine, dataset_id=settings.expected_dataset_id)
    except KnowledgeBaseUnavailable as exc:
        raise ApiError(503, "KNOWLEDGE_BASE_NOT_READY", str(exc)) from exc
    except SQLAlchemyError as exc:
        raise ApiError(
            503,
            "DATABASE_NOT_READY",
            "The knowledge database is unavailable",
        ) from exc
    return KnowledgeStatusResponse.model_validate(result)


@knowledge_router.post(
    "/retrieve",
    responses={422: ERROR_RESPONSE, 503: ERROR_RESPONSE, 500: ERROR_RESPONSE},
)
def retrieve(
    request: Request,
    payload: RetrievalRequest,
    engine: EngineDep,
    settings: SettingsDep,
) -> RetrievalResponse:
    try:
        result = retrieve_knowledge(engine, _provider(request), settings, payload.query)
    except (EmbeddingError, RetrievalError) as exc:
        raise ApiError(
            503,
            "RETRIEVAL_NOT_READY",
            "Closed-knowledge retrieval is not ready",
        ) from exc
    except SQLAlchemyError as exc:
        raise ApiError(
            503,
            "DATABASE_NOT_READY",
            "The knowledge database is unavailable",
        ) from exc
    return RetrievalResponse.model_validate(result.as_dict())


@knowledge_router.post(
    "/answer",
    responses={422: ERROR_RESPONSE, 503: ERROR_RESPONSE, 500: ERROR_RESPONSE},
)
def answer(
    request: Request,
    payload: RetrievalRequest,
    engine: EngineDep,
    settings: SettingsDep,
) -> GroundedAnswer:
    try:
        result = answer_knowledge(
            engine,
            _provider(request),
            _llm_provider(request),
            settings,
            payload.query,
        )
    except LLMError as exc:
        raise ApiError(
            503,
            "LLM_NOT_READY",
            "The grounded-answer provider is not ready",
        ) from exc
    except (AnsweringError, EmbeddingError, RetrievalError) as exc:
        raise ApiError(
            503,
            "ANSWERING_NOT_READY",
            "The grounded-answer pipeline is not ready",
        ) from exc
    except SQLAlchemyError as exc:
        raise ApiError(
            503,
            "DATABASE_NOT_READY",
            "The knowledge database is unavailable",
        ) from exc
    return result
