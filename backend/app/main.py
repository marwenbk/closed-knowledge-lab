from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy import Engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.db import get_engine
from app.services import KnowledgeBaseUnavailable, kb_status, readiness

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
            value = getattr(record, field, None)
            if value is not None:
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
    return str(getattr(request.state, "request_id", uuid4()))


def _error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": _request_id(request),
            }
        },
    )


def create_app(engine: Engine | None = None) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    database = engine or get_engine()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("starting %s", settings.app_name)
        yield
        database.dispose()

    application = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next: Callable[..., Any]) -> Response:
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

    @application.exception_handler(StarletteHTTPException)
    async def http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error_response(request, exc.status_code, "HTTP_ERROR", str(exc.detail))

    @application.exception_handler(RequestValidationError)
    async def validation_exception(request: Request, _: RequestValidationError) -> JSONResponse:
        return _error_response(request, 422, "VALIDATION_ERROR", "The request is invalid")

    @application.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled request error", exc_info=exc)
        return _error_response(request, 500, "INTERNAL_ERROR", "The request could not be completed")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "topmed-api"}

    @application.get("/ready")
    def ready() -> JSONResponse:
        result = readiness(
            database,
            expected_dataset_id=settings.expected_dataset_id,
            expected_dataset_version=settings.expected_dataset_version,
        )
        return JSONResponse(status_code=200 if result["status"] == "ready" else 503, content=result)

    @application.get("/api/v1/kb/status")
    def knowledge_status(request: Request) -> JSONResponse:
        try:
            return JSONResponse(content=kb_status(database))
        except KnowledgeBaseUnavailable as exc:
            return _error_response(request, 503, "KNOWLEDGE_BASE_NOT_READY", str(exc))
        except Exception as exc:
            logger.warning("knowledge status unavailable: %s", exc)
            return _error_response(
                request,
                503,
                "DATABASE_NOT_READY",
                "The knowledge database is unavailable",
            )

    return application


app = create_app()
