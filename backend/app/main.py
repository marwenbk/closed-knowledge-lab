from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy import Engine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

from app.api import ApiError, knowledge_router, system_router
from app.config import Settings, get_settings
from app.db import get_engine
from app.embeddings import EmbeddingProvider

logger = logging.getLogger("topmed.api")


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ("request_id", "method", "path", "status_code", "duration_ms"):
            if (value := getattr(record, field, None)) is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    topmed_logger = logging.getLogger("topmed")
    if not topmed_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        topmed_logger.addHandler(handler)
    topmed_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    topmed_logger.propagate = False


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", None) or uuid4())


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    response_headers = dict(headers or {})
    response_headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
            }
        },
        headers=response_headers,
    )


def create_app(
    engine: Engine | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    configured_settings = get_settings() if settings is None else settings
    configure_logging(configured_settings.log_level)
    owns_engine = engine is None
    database = get_engine() if engine is None else engine

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("starting %s", configured_settings.app_name)
        try:
            yield
        finally:
            if owns_engine:
                database.dispose()

    application = FastAPI(
        title=configured_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.engine = database
    application.state.settings = configured_settings
    application.state.embedding_provider = embedding_provider
    application.state.embedding_provider_lock = threading.Lock()

    @application.middleware("http")
    async def request_context(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started_at = time.perf_counter()
        supplied = request.headers.get("X-Request-ID")
        try:
            request_id = str(UUID(supplied)) if supplied else str(uuid4())
        except ValueError:
            request_id = str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            },
        )
        return response

    @application.exception_handler(ApiError)
    async def api_exception(request: Request, exc: ApiError) -> JSONResponse:
        logger.warning(
            "request_failed",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"request_id": _request_id(request), "status_code": exc.status_code},
        )
        return _error_response(request, exc.status_code, exc.code, str(exc))

    @application.exception_handler(StarletteHTTPException)
    async def http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "The request is invalid"
        return _error_response(request, exc.status_code, "HTTP_ERROR", message, exc.headers)

    @application.exception_handler(RequestValidationError)
    async def validation_exception(request: Request, _: RequestValidationError) -> JSONResponse:
        return _error_response(request, 422, "VALIDATION_ERROR", "The request is invalid")

    @application.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled_request_error",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={
                "request_id": _request_id(request),
                "method": request.method,
                "path": request.url.path,
                "status_code": 500,
            },
        )
        return _error_response(
            request,
            500,
            "INTERNAL_ERROR",
            "The request could not be completed",
        )

    application.include_router(system_router)
    application.include_router(knowledge_router)
    return application


app = create_app()
