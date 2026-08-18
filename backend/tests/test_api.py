from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from app.answering import Citation, GroundedAnswer, ModelIdentity
from app.api import embedding_provider
from app.config import Settings
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from starlette.requests import Request


class StubEmbeddingProvider:
    model_id = "test/model"
    model_version = "a" * 40
    dimensions = 384

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError(f"unexpected document embedding request: {texts!r}")

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError(f"unexpected query embedding request: {texts!r}")


class StubLLMProvider:
    provider_id = "test"

    def __init__(self) -> None:
        settings = Settings(_env_file=None)
        self.model_id = settings.chat_model
        self.model_version = settings.chat_model

    def ensure_ready(self) -> str:
        return self.model_version

    def structured_generate(
        self,
        messages: Sequence[Mapping[str, str]],
        response_model: type[object],
    ) -> object:
        raise AssertionError(f"unexpected generation request: {messages!r}, {response_model!r}")

    def close(self) -> None:
        pass


@pytest.fixture
def application() -> Iterator[FastAPI]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    yield create_app(engine, StubEmbeddingProvider(), StubLLMProvider())
    engine.dispose()


@pytest.fixture
def client(application: FastAPI) -> Iterator[TestClient]:
    with TestClient(application, raise_server_exceptions=False) as test_client:
        yield test_client


def test_health_does_not_require_postgresql(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "topmed-api"}
    UUID(response.headers["X-Request-ID"])


def test_invalid_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "not-a-uuid"})

    assert response.headers["X-Request-ID"] != "not-a-uuid"
    UUID(response.headers["X-Request-ID"])


def test_cors_allows_only_configured_widget_origins(client: TestClient) -> None:
    allowed = client.options(
        "/api/v1/widget/sessions",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    denied = client.options(
        "/api/v1/widget/sessions",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:3000"
    assert allowed.headers["Access-Control-Allow-Credentials"] == "true"
    assert "X-CSRF-Token" in allowed.headers["Access-Control-Allow-Headers"]
    assert "POST" in allowed.headers["Access-Control-Allow-Methods"]
    assert "Access-Control-Allow-Origin" not in denied.headers


def test_valid_request_id_is_preserved_and_errors_use_the_common_shape(
    client: TestClient,
) -> None:
    request_id = "b9cb7f73-3f53-4c40-8393-24af9c90c343"
    response = client.get("/missing", headers={"X-Request-ID": request_id})

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == request_id
    assert response.json() == {
        "error": {
            "code": "HTTP_ERROR",
            "message": "Not Found",
            "request_id": request_id,
        }
    }


def test_readiness_and_status_fail_closed_without_postgresql(client: TestClient) -> None:
    ready_response = client.get("/ready")
    status_response = client.get("/api/v1/kb/status")

    assert ready_response.status_code == 503
    assert ready_response.json()["status"] == "not_ready"
    assert ready_response.json()["checks"]["semantic_index"]["status"] == "pending"
    assert status_response.status_code == 503
    assert status_response.json()["error"]["code"] == "DATABASE_NOT_READY"


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "   "},
        {"query": "x" * 1001},
        {"query": 123},
        {"query": "planos", "unexpected": True},
        {"query": "plano\x00família"},
        {"query": "plano\nfamília"},
        {"query": "plano\x7ffamília"},
    ],
)
@pytest.mark.parametrize("path", ["/api/v1/kb/retrieve", "/api/v1/kb/answer"])
def test_knowledge_queries_reject_invalid_payloads_without_loading_models(
    payload: dict[str, object],
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    application = create_app(engine)
    factory = MagicMock()
    llm_factory = MagicMock()
    monkeypatch.setattr("app.api.OnnxE5EmbeddingProvider", factory)
    monkeypatch.setattr("app.api.DeepSeekProvider", llm_factory)

    with TestClient(application, raise_server_exceptions=False) as test_client:
        response = test_client.post(path, json=payload)

    engine.dispose()
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    UUID(response.headers["X-Request-ID"])
    factory.assert_not_called()
    llm_factory.assert_not_called()


def test_openapi_describes_typed_success_and_error_contracts(application: FastAPI) -> None:
    schema = application.openapi()
    retrieval = schema["paths"]["/api/v1/kb/retrieve"]["post"]
    answer = schema["paths"]["/api/v1/kb/answer"]["post"]
    readiness = schema["paths"]["/ready"]["get"]
    widget_session = schema["paths"]["/api/v1/widget/sessions"]["post"]
    widget_message = schema["paths"]["/api/v1/widget/conversations/{conversation_id}/messages"][
        "post"
    ]
    widget_events = schema["paths"]["/api/v1/widget/conversations/{conversation_id}/events"]["get"]
    widget_handoff = schema["paths"][
        "/api/v1/widget/conversations/{conversation_id}/request-human"
    ]["post"]
    admin_login = schema["paths"]["/api/v1/admin/auth/login"]["post"]
    admin_queue = schema["paths"]["/api/v1/admin/handoffs"]["get"]
    admin_events = schema["paths"]["/api/v1/admin/events"]["get"]

    assert retrieval["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/RetrievalRequest"
    )
    assert retrieval["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/RetrievalResponse"
    )
    assert retrieval["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ErrorResponse"
    )
    assert readiness["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ReadinessResponse"
    )
    assert readiness["responses"]["503"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ReadinessResponse"
    )
    assert retrieval["tags"] == ["knowledge-base"]
    assert answer["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/RetrievalRequest"
    )
    assert answer["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/GroundedAnswer"
    )
    assert widget_session["responses"]["201"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/WidgetSessionResponse")
    widget_message_schema = widget_message["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert {option["$ref"].rsplit("/", 1)[-1] for option in widget_message_schema["anyOf"]} == {
        "MessageResponse",
        "HumanQueueMessageResponse",
    }
    assert widget_events["responses"]["200"]["content"]["text/event-stream"]
    assert widget_message["security"] == [{"HTTPBearer": []}]
    assert widget_handoff["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/HandoffResponse")
    assert admin_login["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/AdminIdentityResponse")
    assert admin_queue["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/HandoffQueueResponse")
    assert admin_events["responses"]["200"]["content"]["text/event-stream"]


def test_answer_endpoint_returns_only_the_grounded_contract(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = GroundedAnswer(
        status="ANSWERABLE",
        answer="O plano Família permite até 3 dependentes.",
        citations=(
            Citation(
                citation_id="c1",
                chunk_id=UUID(int=1),
                stable_chunk_key="family-members__limits__001",
                document_key="family-members",
                document="Membros da família e dependentes",
                section="Limites de dependentes",
                quote="O plano Família permite o cadastro de até 3 dependentes.",
            ),
        ),
        dataset_id="topmed-demo",
        dataset_version="2.0.0",
        model=ModelIdentity(
            provider="test",
            name="deepseek-v4-flash",
            version="deepseek-v4-flash",
            prompt_version="1.0.0",
        ),
        verification_status="VERIFIED",
        regenerated=False,
        duration_ms=10.0,
    )
    monkeypatch.setattr("app.api.answer_knowledge", lambda *_: result)

    response = client.post(
        "/api/v1/kb/answer",
        json={"query": "Quantos dependentes o plano Família permite?"},
    )

    assert response.status_code == 200
    assert response.json()["verification_status"] == "VERIFIED"
    assert response.json()["citations"][0]["citation_id"] == "c1"


def test_method_not_allowed_preserves_headers(client: TestClient) -> None:
    response = client.get("/api/v1/kb/retrieve")

    assert response.status_code == 405
    assert response.headers["Allow"] == "POST"
    UUID(response.headers["X-Request-ID"])


def test_unexpected_error_keeps_request_id_in_header_and_body(application: FastAPI) -> None:
    @application.get("/test-only-unexpected-error")
    def unexpected_error() -> None:
        raise RuntimeError("internal details must not be returned")

    request_id = "5d2422d8-a499-4b1f-8f6e-860483e38095"
    with (
        patch("app.main.logger.error") as error_log,
        TestClient(application, raise_server_exceptions=False) as test_client,
    ):
        response = test_client.get(
            "/test-only-unexpected-error",
            headers={"X-Request-ID": request_id},
        )

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == request_id
    assert response.json()["error"]["request_id"] == request_id
    assert "internal details" not in response.text
    assert error_log.call_args.kwargs["extra"] == {
        "request_id": request_id,
        "method": "GET",
        "path": "/test-only-unexpected-error",
        "status_code": 500,
    }


def test_injected_engine_is_not_disposed_by_lifespan() -> None:
    engine = MagicMock(spec=Engine)
    with TestClient(create_app(engine), raise_server_exceptions=False) as test_client:
        assert test_client.get("/health").status_code == 200
    engine.dispose.assert_not_called()


def test_app_owned_engine_is_disposed_by_lifespan() -> None:
    engine = MagicMock(spec=Engine)
    with (
        patch("app.main.get_engine", return_value=engine),
        TestClient(create_app(), raise_server_exceptions=False) as test_client,
    ):
        assert test_client.get("/health").status_code == 200
    engine.dispose.assert_called_once_with()


def test_lazy_embedding_provider_initializes_once_across_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = create_app(create_engine("sqlite+pysqlite:///:memory:"))
    provider = StubEmbeddingProvider()
    call_lock = threading.Lock()
    call_count = 0

    def provider_factory(_: object) -> StubEmbeddingProvider:
        nonlocal call_count
        with call_lock:
            call_count += 1
        time.sleep(0.02)
        return provider

    monkeypatch.setattr("app.api.OnnxE5EmbeddingProvider", provider_factory)
    request = Request({"type": "http", "app": application})
    with ThreadPoolExecutor(max_workers=8) as executor:
        resolved = list(executor.map(lambda _: embedding_provider(request), range(16)))

    assert all(item is provider for item in resolved)
    assert call_count == 1
