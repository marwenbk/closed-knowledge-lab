from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.main import create_app


def test_health_does_not_require_postgresql() -> None:
    application = create_app(create_engine("sqlite+pysqlite:///:memory:"))
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "topmed-api"}
    UUID(response.headers["X-Request-ID"])


def test_invalid_request_id_is_replaced() -> None:
    application = create_app(create_engine("sqlite+pysqlite:///:memory:"))
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/health", headers={"X-Request-ID": "not-a-uuid"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "not-a-uuid"
    UUID(response.headers["X-Request-ID"])


def test_valid_request_id_is_preserved_and_errors_use_the_common_shape() -> None:
    application = create_app(create_engine("sqlite+pysqlite:///:memory:"))
    request_id = "b9cb7f73-3f53-4c40-8393-24af9c90c343"
    with TestClient(application, raise_server_exceptions=False) as client:
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


def test_readiness_and_status_fail_closed_without_postgresql() -> None:
    application = create_app(create_engine("sqlite+pysqlite:///:memory:"))
    with TestClient(application, raise_server_exceptions=False) as client:
        ready_response = client.get("/ready")
        status_response = client.get("/api/v1/kb/status")

    assert ready_response.status_code == 503
    assert ready_response.json()["status"] == "not_ready"
    assert ready_response.json()["checks"]["semantic_index"]["status"] == "pending"
    assert status_response.status_code == 503
    assert status_response.json()["error"]["code"] == "DATABASE_NOT_READY"
