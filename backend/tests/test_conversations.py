from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from app.answering import (
    AnswerExecution,
    Citation,
    GroundedAnswer,
    ModelIdentity,
    contextualize_query,
)
from app.config import Settings
from app.conversations import (
    VALID_TRANSITIONS,
    WidgetPrincipal,
    authenticate_widget_session,
    load_events,
)
from app.llm import LLMError
from app.main import create_app
from app.models import (
    AuditEvent,
    ConversationEvent,
    KnowledgeBaseVersion,
    Message,
    RagRun,
    WidgetSession,
)
from app.widget_api import event_stream
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from starlette.requests import Request

ORIGIN = "http://localhost:3000"


class StubEmbeddingProvider:
    model_id = "test/embedding"
    model_version = "e" * 40
    dimensions = 384

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError(f"unexpected embedding request: {texts!r}")

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError(f"unexpected embedding request: {texts!r}")


class StubLLMProvider:
    provider_id = "test"
    model_id = "deepseek-v4-flash"
    model_version = "deepseek-v4-flash"

    def ensure_ready(self) -> str:
        return self.model_version

    def structured_generate(
        self,
        messages: Sequence[Mapping[str, str]],
        response_model: type[Any],
    ) -> Any:
        raise AssertionError(f"unexpected generation request: {messages!r}, {response_model!r}")

    def close(self) -> None:
        pass


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        widget_assistant_key="local-assistant-key",
        widget_token_secret="test-widget-token-secret-at-least-32-characters",
        widget_allowed_origins=ORIGIN,
        sse_poll_interval_seconds=0.001,
        sse_keepalive_seconds=0.001,
    )


def _answer() -> GroundedAnswer:
    return GroundedAnswer(
        status="ANSWERABLE",
        answer="The Family plan allows up to three dependents.",
        citations=(
            Citation(
                citation_id="c1",
                chunk_id=UUID(int=1),
                stable_chunk_key="family-members__limits__001",
                document_key="family-members",
                document="Familiares e dependentes",
                section="Dependent limits",
                quote="The Family plan allows up to three dependents.",
            ),
        ),
        dataset_id="topmed-demo",
        dataset_version="3.0.0",
        model=ModelIdentity(
            provider="test",
            name="deepseek-v4-flash",
            version="deepseek-v4-flash",
            prompt_version="1.0.0",
        ),
        verification_status="VERIFIED",
        regenerated=False,
        duration_ms=12.5,
    )


def _seed_active_version(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
        session.add(
            KnowledgeBaseVersion(
                id=uuid4(),
                dataset_id="topmed-demo",
                dataset_version="3.0.0",
                generator_version="1.0.0",
                language="en-US",
                seed_checksum="a" * 64,
                template_checksum="b" * 64,
                manifest_checksum="c" * 64,
                status="ACTIVE",
                activated_at=datetime.now(UTC),
            )
        )


def _start_session(
    client: TestClient, assistant_key: str = "local-assistant-key"
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/widget/sessions",
        headers={"Origin": ORIGIN},
        json={"assistant_key": assistant_key},
    )
    assert response.status_code == 201
    return response.json()


def _auth(token: str, origin: str = ORIGIN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Origin": origin}


def test_conversation_transition_graph_is_explicit_and_terminal() -> None:
    assert VALID_TRANSITIONS["AI_ACTIVE"] == {
        "AI_REVIEW_PENDING",
        "HUMAN_REQUESTED",
        "CLOSED",
    }
    assert VALID_TRANSITIONS["HUMAN_ACTIVE"] == {"RETURNED_TO_AI", "CLOSED"}
    assert VALID_TRANSITIONS["CLOSED"] == set()


def test_conversation_context_resolves_references_without_becoming_evidence() -> None:
    query = contextualize_query(
        "And how many dependents does it allow?",
        ["I have the employer Gold benefit."],
    )

    assert query.startswith("Current question: And how many dependents does it allow?")
    assert query.endswith("Previous customer context: I have the employer Gold benefit.")
    assert contextualize_query("What is the price?", []) == "What is the price?"


def test_event_stream_emits_keepalive(monkeypatch: pytest.MonkeyPatch) -> None:
    request = AsyncMock(spec=Request)
    request.is_disconnected.return_value = False
    principal = WidgetPrincipal(
        session_id=uuid4(),
        anonymous_subject="test",
        origin=ORIGIN,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    monkeypatch.setattr("app.widget_api.load_events", lambda *_args, **_kwargs: ())

    async def next_event() -> Any:
        stream = event_stream(
            request,
            object(),
            principal,
            uuid4(),
            (),
            initial_cursor=0,
            poll_interval=0.001,
            keepalive_interval=0.001,
            replay_limit=10,
        )
        return await anext(stream)

    event = asyncio.run(next_event())
    assert event.event == "keepalive"
    assert "timestamp" in event.data


@pytest.mark.postgres
def test_widget_conversation_persistence_idempotency_and_replay(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    _seed_active_version(postgres_engine)
    calls: list[str] = []

    def answer_stub(*args: Any, **_kwargs: Any) -> AnswerExecution:
        calls.append(str(args[-1]))
        return AnswerExecution(_answer(), {"source": "test"})

    monkeypatch.setattr("app.conversations.answer_knowledge_with_trace", answer_stub)
    application = create_app(
        postgres_engine,
        StubEmbeddingProvider(),
        StubLLMProvider(),
        settings,
    )
    with TestClient(application, raise_server_exceptions=False) as client:
        invalid_key = client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": ORIGIN},
            json={"assistant_key": "wrong"},
        )
        invalid_origin = client.post(
            "/api/v1/widget/sessions",
            headers={"Origin": "https://evil.example"},
            json={"assistant_key": "local-assistant-key"},
        )
        session_data = _start_session(client)
        headers = _auth(session_data["token"])
        wrong_origin = client.post(
            "/api/v1/widget/conversations",
            headers=_auth(session_data["token"], "https://evil.example"),
            json={},
        )
        conversation_response = client.post(
            "/api/v1/widget/conversations", headers=headers, json={}
        )
        conversation_id = conversation_response.json()["conversation_id"]
        other_session = _start_session(client)
        cross_session_read = client.get(
            f"/api/v1/widget/conversations/{conversation_id}",
            headers=_auth(other_session["token"]),
        )
        client_message_id = str(uuid4())
        first = client.post(
            f"/api/v1/widget/conversations/{conversation_id}/messages",
            headers=headers,
            json={
                "content": "How many dependents does the Family plan allow?",
                "client_message_id": client_message_id,
            },
        )
        repeated = client.post(
            f"/api/v1/widget/conversations/{conversation_id}/messages",
            headers=headers,
            json={
                "content": "How many dependents does the Family plan allow?",
                "client_message_id": client_message_id,
            },
        )
        reused_with_different_content = client.post(
            f"/api/v1/widget/conversations/{conversation_id}/messages",
            headers=headers,
            json={"content": "Different content", "client_message_id": client_message_id},
        )
        restored = client.post(
            "/api/v1/widget/conversations",
            headers=headers,
            json={"conversation_id": conversation_id},
        )
        read = client.get(f"/api/v1/widget/conversations/{conversation_id}", headers=headers)
        missing_auth = client.get(f"/api/v1/widget/conversations/{conversation_id}/events")
        closed = client.post(
            f"/api/v1/widget/conversations/{conversation_id}/close", headers=headers
        )
        after_close = client.post(
            f"/api/v1/widget/conversations/{conversation_id}/messages",
            headers=headers,
            json={"content": "Another question", "client_message_id": str(uuid4())},
        )

    assert invalid_key.status_code == 401
    assert invalid_origin.status_code == 401
    assert wrong_origin.status_code == 403
    assert cross_session_read.status_code == 404
    assert conversation_response.status_code == 201
    assert first.status_code == 200
    assert repeated.status_code == 200
    assert first.json() == repeated.json()
    assert reused_with_different_content.status_code == 409
    assert reused_with_different_content.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert calls == ["How many dependents does the Family plan allow?"]
    assert restored.status_code == 200
    assert read.status_code == 200
    assert [message["sender"]["type"] for message in read.json()["messages"]] == [
        "CUSTOMER",
        "AI",
    ]
    assert missing_auth.status_code == 401, missing_auth.text
    assert closed.status_code == 200
    assert closed.json()["state"] == "CLOSED"
    assert after_close.status_code == 409
    assert after_close.json()["error"]["code"] == "CONVERSATION_CLOSED"

    principal = authenticate_widget_session(
        postgres_engine,
        settings,
        token=session_data["token"],
        origin=ORIGIN,
    )
    all_events = load_events(
        postgres_engine,
        principal,
        UUID(conversation_id),
        after_id=0,
        limit=100,
    )
    replayed = load_events(
        postgres_engine,
        principal,
        UUID(conversation_id),
        after_id=all_events[2].event_id,
        limit=100,
    )
    assert [event.event_id for event in all_events] == sorted(
        event.event_id for event in all_events
    )
    assert all(event.event_id > all_events[2].event_id for event in replayed)
    assert {event.event_type for event in all_events}.issuperset(
        {
            "conversation.created",
            "processing.started",
            "message.created",
            "message.delivered",
            "conversation.closed",
        }
    )

    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(WidgetSession)) == 2
        assert session.scalar(select(func.count()).select_from(Message)) == 2
        assert session.scalar(select(func.count()).select_from(RagRun)) == 1
        run = session.scalar(select(RagRun))
        assert run is not None
        assert run.status == "COMPLETED"
        assert run.answerability_status == "ANSWERABLE"
        assert run.kb_version_id is not None
        assert run.conversation_context_json == []
        assert run.execution_trace_json == {"source": "test"}
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 3
        event_id = session.scalar(select(ConversationEvent.id).limit(1))
        with pytest.raises(DBAPIError, match="append-only"), session.begin_nested():
            session.execute(
                text("UPDATE conversation_events SET event_type = 'changed' WHERE id = :id"),
                {"id": event_id},
            )


@pytest.mark.postgres
def test_failed_generation_is_persisted_and_not_retried(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    _seed_active_version(postgres_engine)
    calls = 0

    def fail_answer(*_args: Any, **_kwargs: Any) -> AnswerExecution:
        nonlocal calls
        calls += 1
        raise LLMError("synthetic failure")

    monkeypatch.setattr("app.conversations.answer_knowledge_with_trace", fail_answer)
    application = create_app(
        postgres_engine,
        StubEmbeddingProvider(),
        StubLLMProvider(),
        settings,
    )
    with TestClient(application, raise_server_exceptions=False) as client:
        session_data = _start_session(client)
        headers = _auth(session_data["token"])
        conversation = client.post("/api/v1/widget/conversations", headers=headers, json={}).json()
        message_id = str(uuid4())
        payload = {"content": "Fail safely", "client_message_id": message_id}
        first = client.post(
            f"/api/v1/widget/conversations/{conversation['conversation_id']}/messages",
            headers=headers,
            json=payload,
        )
        repeated = client.post(
            f"/api/v1/widget/conversations/{conversation['conversation_id']}/messages",
            headers=headers,
            json=payload,
        )

    assert first.status_code == 503
    assert first.json()["error"]["code"] == "LLM_NOT_READY"
    assert repeated.status_code == 503
    assert repeated.json()["error"]["code"] == "MESSAGE_FAILED"
    assert calls == 1
    with Session(postgres_engine) as session:
        run = session.scalar(select(RagRun))
        assert run is not None
        assert run.status == "FAILED"
        assert run.error_code == "LLMERROR"
