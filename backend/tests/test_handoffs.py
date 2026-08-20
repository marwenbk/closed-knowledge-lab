from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from app.admin_api import (
    AdminEventSubscription,
    _event_subscription,
    _set_auth_cookies,
    admin_event_stream,
)
from app.admin_auth import (
    ADMIN_CSRF_COOKIE,
    ADMIN_SESSION_COOKIE,
    PASSWORD_HASHER,
    AdminAuthError,
    AdminPrincipal,
    bootstrap_admin,
    validate_bootstrap_password,
)
from app.answering import AnswerExecution, GroundedAnswer, ModelIdentity, VerificationDecision
from app.config import Settings
from app.conversations import authenticate_widget_session, load_events
from app.handoffs import load_admin_events
from app.main import create_app
from app.models import (
    AdminSession,
    AdminUser,
    AdminUserRole,
    AuditEvent,
    Chunk,
    Conversation,
    Document,
    DocumentRevision,
    HandoffEvent,
    KnowledgeBaseVersion,
    Message,
    MessageReview,
    RagRun,
)
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from starlette.requests import Request

ORIGIN = "http://localhost:3000"
ADMIN_PASSWORD = "correct horse battery staple"


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


class RejectingReviewProvider(StubLLMProvider):
    def structured_generate(
        self,
        _messages: Sequence[Mapping[str, str]],
        response_model: type[Any],
    ) -> Any:
        assert response_model is VerificationDecision
        return VerificationDecision(supported=False, issues=["unsupported edit"])


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        widget_assistant_key="local-assistant-key",
        widget_token_secret="test-widget-token-secret-at-least-32-characters",
        widget_allowed_origins=ORIGIN,
        admin_allowed_origins=ORIGIN,
        admin_session_ttl_seconds=28_800,
        sse_poll_interval_seconds=0.001,
        sse_keepalive_seconds=0.001,
    )


def _bootstrap(
    engine: Engine,
    email: str = "admin@topmed.local",
    role: str = "ADMIN",
) -> UUID:
    result = bootstrap_admin(
        engine,
        email=email,
        display_name=email.split("@", 1)[0].title(),
        password=ADMIN_PASSWORD,
    )
    if role != "ADMIN":
        with Session(engine) as session, session.begin():
            session.execute(delete(AdminUserRole).where(AdminUserRole.user_id == result.user_id))
            session.add(AdminUserRole(user_id=result.user_id, role=role))
    return result.user_id


def _login(client: TestClient, email: str = "admin@topmed.local") -> dict[str, str]:
    response = client.post(
        "/api/v1/admin/auth/login",
        headers={"Origin": ORIGIN},
        json={"email": email, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200, response.text
    csrf = client.cookies.get(ADMIN_CSRF_COOKIE)
    assert csrf
    return {"Origin": ORIGIN, "X-CSRF-Token": csrf}


def _widget_conversation(client: TestClient) -> tuple[str, dict[str, str]]:
    widget_session = client.post(
        "/api/v1/widget/sessions",
        headers={"Origin": ORIGIN},
        json={"assistant_key": "local-assistant-key"},
    )
    assert widget_session.status_code == 201
    headers = {
        "Origin": ORIGIN,
        "Authorization": f"Bearer {widget_session.json()['token']}",
    }
    conversation = client.post("/api/v1/widget/conversations", headers=headers, json={})
    assert conversation.status_code == 201
    return conversation.json()["conversation_id"], headers


def _seed_active_version(engine: Engine, *, active: bool = True) -> UUID:
    version_id = uuid4()
    with Session(engine) as session, session.begin():
        session.add(
            KnowledgeBaseVersion(
                id=version_id,
                dataset_id="topmed-demo",
                dataset_version="2.0.0",
                generator_version="1.0.0",
                language="pt-BR",
                seed_checksum="a" * 64,
                template_checksum="b" * 64,
                manifest_checksum="c" * 64,
                status="ACTIVE" if active else "DRAFT",
                activated_at=datetime.now(UTC) if active else None,
            )
        )
    return version_id


def _answer(status: str = "ANSWERABLE") -> GroundedAnswer:
    return GroundedAnswer(
        status=status,
        answer=(
            "As fontes aprovadas apresentam regras conflitantes; "
            "uma pessoa continuará o atendimento."
            if status == "CONFLICTING_EVIDENCE"
            else "Resposta verificada."
        ),
        citations=(),
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
        duration_ms=5.0,
    )


@pytest.mark.postgres
def test_reviewed_ai_proposal_is_hidden_until_approved(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings().model_copy(update={"review_before_send_enabled": True})
    _seed_active_version(postgres_engine)
    _bootstrap(postgres_engine)
    monkeypatch.setattr(
        "app.conversations.answer_knowledge_with_trace",
        lambda *_args, **_kwargs: AnswerExecution(_answer(), {"retrieval": {}}),
    )
    application = create_app(
        postgres_engine,
        StubEmbeddingProvider(),
        StubLLMProvider(),
        settings,
    )

    with TestClient(application, raise_server_exceptions=False) as client:
        conversation_id, widget_headers = _widget_conversation(client)
        submitted = client.post(
            f"/api/v1/widget/conversations/{conversation_id}/messages",
            headers=widget_headers,
            json={"content": "Pergunta revisada", "client_message_id": str(uuid4())},
        )
        hidden = client.get(
            f"/api/v1/widget/conversations/{conversation_id}", headers=widget_headers
        )
        admin_headers = _login(client)
        admin_view = client.get(f"/api/v1/admin/conversations/{conversation_id}")
        proposal = next(
            message
            for message in admin_view.json()["messages"]
            if message["review_status"] == "PENDING"
        )
        approved = client.post(
            f"/api/v1/admin/messages/{proposal['message_id']}/review",
            headers=admin_headers,
            json={"action": "APPROVE", "content": None, "note": None},
        )
        delivered = client.get(
            f"/api/v1/widget/conversations/{conversation_id}", headers=widget_headers
        )

    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["delivery_mode"] == "REVIEW_PENDING"
    assert hidden.json()["state"] == "AI_REVIEW_PENDING"
    assert all(message["sender"]["type"] != "AI" for message in hidden.json()["messages"])
    assert proposal["visibility"] == "INTERNAL"
    assert approved.status_code == 200, approved.text
    assert delivered.json()["state"] == "AI_ACTIVE"
    assert any(message["sender"]["type"] == "AI" for message in delivered.json()["messages"])
    with Session(postgres_engine) as session:
        message = session.get(Message, UUID(proposal["message_id"]))
        assert message is not None
        assert message.review_status == "APPROVED"
        assert message.visibility == "PUBLIC"
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "message.reviewed")
        )
        assert audit is not None and audit.actor_type == "ADMIN"
        review = session.scalar(select(MessageReview).where(MessageReview.message_id == message.id))
        assert review is not None and review.action == "APPROVE"
    with Session(postgres_engine) as session, session.begin():
        review = session.scalar(select(MessageReview).limit(1))
        assert review is not None
        review.note = "mutated"
        with pytest.raises(DBAPIError, match="append-only"):
            session.flush()


@pytest.mark.postgres
def test_review_edit_fails_closed_and_regeneration_is_limited_to_once(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings().model_copy(update={"review_before_send_enabled": True})
    _seed_active_version(postgres_engine)
    _bootstrap(postgres_engine)
    first_answer = _answer()
    regenerated_answer = first_answer.model_copy(
        update={"answer": "Resposta regenerada e verificada."}
    )
    monkeypatch.setattr(
        "app.conversations.answer_knowledge_with_trace",
        lambda *_args, **_kwargs: AnswerExecution(first_answer, {"retrieval": {}}),
    )
    monkeypatch.setattr("app.reviews._retrieval", lambda _trace: MagicMock())
    monkeypatch.setattr(
        "app.reviews.answer_knowledge_with_trace",
        lambda *_args, **_kwargs: AnswerExecution(regenerated_answer, {"retrieval": {}}),
    )
    application = create_app(
        postgres_engine,
        StubEmbeddingProvider(),
        RejectingReviewProvider(),
        settings,
    )

    with TestClient(application, raise_server_exceptions=False) as client:
        conversation_id, widget_headers = _widget_conversation(client)
        client.post(
            f"/api/v1/widget/conversations/{conversation_id}/messages",
            headers=widget_headers,
            json={"content": "Pergunta revisada", "client_message_id": str(uuid4())},
        )
        headers = _login(client)
        admin_view = client.get(f"/api/v1/admin/conversations/{conversation_id}").json()
        proposal = next(
            message for message in admin_view["messages"] if message["review_status"] == "PENDING"
        )
        path = f"/api/v1/admin/messages/{proposal['message_id']}/review"
        invalid_edit = client.post(
            path,
            headers=headers,
            json={"action": "EDIT_AND_SEND", "content": "Afirmação inventada.", "note": None},
        )
        regenerated = client.post(
            path,
            headers=headers,
            json={"action": "REJECT_AND_REGENERATE", "content": None, "note": "Tente novamente"},
        )
        repeated = client.post(
            path,
            headers=headers,
            json={"action": "REJECT_AND_REGENERATE", "content": None, "note": None},
        )

    assert invalid_edit.status_code == 409
    assert invalid_edit.json()["error"]["code"] == "REVIEW_EDIT_NOT_GROUNDED"
    assert regenerated.status_code == 200, regenerated.text
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "REVIEW_REGENERATION_LIMIT"
    with Session(postgres_engine) as session:
        proposal_row = session.get(Message, UUID(proposal["message_id"]))
        assert proposal_row is not None
        assert proposal_row.content == "Resposta regenerada e verificada."
        assert proposal_row.review_regeneration_count == 1
        assert proposal_row.review_status == "PENDING"


def test_admin_password_policy_uses_argon2id() -> None:
    with pytest.raises(AdminAuthError, match="known local default"):
        validate_bootstrap_password("password1234")

    password_hash = PASSWORD_HASHER.hash(ADMIN_PASSWORD)

    assert password_hash.startswith("$argon2id$")
    assert PASSWORD_HASHER.verify(password_hash, ADMIN_PASSWORD)


def test_admin_cookie_flags_follow_the_environment() -> None:
    expires_at = datetime.now(UTC) + timedelta(hours=8)
    local_response = Response()
    _set_auth_cookies(
        local_response,
        _settings(),
        session_token="session-token",
        csrf_token="csrf-token",
        expires_at=expires_at,
    )
    production_settings = Settings(
        _env_file=None,
        app_env="production",
        widget_assistant_key="production-assistant-key",
        widget_token_secret="production-widget-token-secret-at-least-32-characters",
        widget_allowed_origins="https://widget.topmed.local",
        admin_allowed_origins="https://admin.topmed.local",
    )
    production_response = Response()
    _set_auth_cookies(
        production_response,
        production_settings,
        session_token="session-token",
        csrf_token="csrf-token",
        expires_at=expires_at,
    )

    local_cookies = local_response.headers.getlist("set-cookie")
    production_cookies = production_response.headers.getlist("set-cookie")
    assert any("HttpOnly" in cookie and "SameSite=lax" in cookie for cookie in local_cookies)
    session_cookie = next(cookie for cookie in local_cookies if ADMIN_SESSION_COOKIE in cookie)
    csrf_cookie = next(cookie for cookie in local_cookies if ADMIN_CSRF_COOKIE in cookie)
    assert "Path=/api/v1/admin" in session_cookie
    assert "HttpOnly" in session_cookie
    assert "Path=/" in csrf_cookie
    assert "HttpOnly" not in csrf_cookie
    assert all("Secure" not in cookie for cookie in local_cookies)
    assert all("Secure" in cookie for cookie in production_cookies)


def test_admin_event_stream_emits_keepalive(monkeypatch: pytest.MonkeyPatch) -> None:
    request = AsyncMock(spec=Request)
    request.is_disconnected.return_value = False
    principal = AdminPrincipal(
        user_id=uuid4(),
        session_id=uuid4(),
        email="admin@topmed.local",
        display_name="TopMed Admin",
        roles=frozenset({"ADMIN"}),
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
        csrf_hash="a" * 64,
    )
    subscription = AdminEventSubscription(principal, (), 0)
    monkeypatch.setattr("app.admin_api.load_admin_events", lambda *_args, **_kwargs: ())

    async def next_event() -> Any:
        stream = admin_event_stream(request, object(), subscription, _settings())
        return await anext(stream)

    event = asyncio.run(next_event())
    assert event.event == "keepalive"
    assert "timestamp" in event.data


def test_fresh_admin_event_stream_replays_only_the_bounded_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = AdminPrincipal(
        user_id=uuid4(),
        session_id=uuid4(),
        email="admin@topmed.local",
        display_name="TopMed Admin",
        roles=frozenset({"ADMIN"}),
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
        csrf_hash="a" * 64,
    )
    settings = _settings().model_copy(update={"sse_replay_limit": 20})
    load = MagicMock(return_value=())
    engine = object()
    monkeypatch.setattr("app.admin_api.latest_admin_event_id", lambda _engine: 75)
    monkeypatch.setattr("app.admin_api.load_admin_events", load)

    subscription = _event_subscription(Response(), principal, engine, settings, None)

    assert subscription.initial_cursor == 55
    load.assert_called_once_with(engine, after_id=55, limit=20)


@pytest.mark.postgres
def test_admin_bootstrap_login_csrf_logout_and_role_enforcement(
    postgres_engine: Engine,
) -> None:
    settings = _settings()
    created = bootstrap_admin(
        postgres_engine,
        email="Admin@TopMed.Local",
        display_name="TopMed Admin",
        password=ADMIN_PASSWORD,
    )
    repeated = bootstrap_admin(
        postgres_engine,
        email="admin@topmed.local",
        display_name="Ignored",
        password=ADMIN_PASSWORD,
    )
    _bootstrap(postgres_engine, "auditor@topmed.local", "AUDITOR")
    application = create_app(
        postgres_engine,
        StubEmbeddingProvider(),
        StubLLMProvider(),
        settings,
    )

    with TestClient(application, raise_server_exceptions=False) as client:
        missing_origin = client.post(
            "/api/v1/admin/auth/login",
            json={"email": "admin@topmed.local", "password": ADMIN_PASSWORD},
        )
        invalid = client.post(
            "/api/v1/admin/auth/login",
            headers={"Origin": ORIGIN},
            json={"email": "admin@topmed.local", "password": "incorrect-password"},
        )
        unknown = client.post(
            "/api/v1/admin/auth/login",
            headers={"Origin": ORIGIN},
            json={"email": "missing@topmed.local", "password": "incorrect-password"},
        )
        authenticated = client.post(
            "/api/v1/admin/auth/login",
            headers={"Origin": ORIGIN},
            json={"email": "admin@topmed.local", "password": ADMIN_PASSWORD},
        )
        session_token = client.cookies.get(ADMIN_SESSION_COOKIE)
        csrf_token = client.cookies.get(ADMIN_CSRF_COOKIE)
        assert session_token and csrf_token
        csrf_headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf_token}
        me = client.get("/api/v1/admin/auth/me", headers={"Origin": ORIGIN})
        missing_csrf = client.post(
            "/api/v1/admin/auth/logout",
            headers={"Origin": ORIGIN},
        )
        logout = client.post("/api/v1/admin/auth/logout", headers=csrf_headers)
        revoked = client.get("/api/v1/admin/auth/me", headers={"Origin": ORIGIN})
        _login(client)
        with Session(postgres_engine) as session, session.begin():
            active_session = session.scalar(
                select(AdminSession)
                .where(
                    AdminSession.user_id == created.user_id,
                    AdminSession.revoked_at.is_(None),
                )
                .order_by(AdminSession.created_at.desc())
                .limit(1)
            )
            assert active_session is not None
            active_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        expired = client.get("/api/v1/admin/auth/me", headers={"Origin": ORIGIN})

    with TestClient(application, raise_server_exceptions=False) as auditor_client:
        _login(auditor_client, "auditor@topmed.local")
        denied = auditor_client.get("/api/v1/admin/handoffs", headers={"Origin": ORIGIN})

    assert created.created is True
    assert repeated.created is False
    assert created.user_id == repeated.user_id
    assert missing_origin.status_code == 403
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "INVALID_ADMIN_CREDENTIALS"
    assert unknown.status_code == 401
    assert unknown.json()["error"]["code"] == "INVALID_ADMIN_CREDENTIALS"
    assert authenticated.status_code == 200
    assert me.status_code == 200
    assert me.json()["roles"] == ["ADMIN"]
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "CSRF_VALIDATION_FAILED"
    assert logout.status_code == 204
    assert revoked.status_code == 401
    assert expired.status_code == 401
    assert denied.status_code == 403

    with Session(postgres_engine) as session:
        user = session.get(AdminUser, created.user_id)
        assert user is not None
        assert user.password_hash != ADMIN_PASSWORD
        assert user.password_hash.startswith("$argon2id$")
        assert (
            session.scalar(select(func.count()).where(AdminSession.token_hash == session_token))
            == 0
        )
        assert session.scalar(select(func.count()).select_from(AdminSession)) == 3
        assert (
            session.scalar(
                select(func.count())
                .select_from(AdminSession)
                .where(AdminSession.revoked_at.is_not(None))
            )
            == 1
        )


@pytest.mark.postgres
def test_admin_dashboard_rag_trace_and_active_knowledge_browser(
    postgres_engine: Engine,
) -> None:
    settings = _settings()
    version_id = _seed_active_version(postgres_engine, active=False)
    _bootstrap(postgres_engine)
    document_id = uuid4()
    revision_id = uuid4()
    chunk_id = uuid4()
    with Session(postgres_engine) as session, session.begin():
        session.add(
            Document(
                id=document_id,
                kb_version_id=version_id,
                document_key="support-hours",
                title="Horários de suporte",
                language="pt-BR",
                source_path="knowledge_base/support-hours.md",
                checksum="d" * 64,
                status="IMPORTED",
                sort_order=1,
                metadata_json={"topic": "support"},
            )
        )
        session.flush()
        session.add(
            DocumentRevision(
                id=revision_id,
                document_id=document_id,
                revision_number=1,
                content_markdown="# Horários\n\nAtendimento de segunda a sexta.",
                content_checksum="d" * 64,
                front_matter={"title": "Horários de suporte"},
            )
        )
        session.flush()
        session.add(
            Chunk(
                id=chunk_id,
                kb_version_id=version_id,
                document_id=document_id,
                revision_id=revision_id,
                stable_chunk_key="support-hours:horarios:1",
                section="Horários",
                section_path=["Horários"],
                ordinal=1,
                content="Atendimento de segunda a sexta.",
                content_normalized="atendimento de segunda a sexta.",
                token_count=6,
                metadata_json={},
            )
        )
        session.flush()
        version = session.get(KnowledgeBaseVersion, version_id)
        assert version is not None
        version.status = "ACTIVE"
        version.activated_at = datetime.now(UTC)
    application = create_app(
        postgres_engine,
        StubEmbeddingProvider(),
        StubLLMProvider(),
        settings,
    )

    with TestClient(application, raise_server_exceptions=False) as client:
        _login(client)
        # Safe reads work on same-origin deployments where browsers omit Origin for GET.
        dashboard = client.get("/api/v1/admin/dashboard")
        versions = client.get("/api/v1/admin/knowledge/versions")
        version_detail = client.get(f"/api/v1/admin/knowledge/versions/{version_id}")
        detail = client.get(
            f"/api/v1/admin/knowledge/versions/{version_id}/documents/{document_id}"
        )
        missing_run = client.get(f"/api/v1/admin/rag-runs/{uuid4()}")

    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["knowledge"] == {
        "dataset_id": "topmed-demo",
        "dataset_version": "2.0.0",
        "status": "ACTIVE",
        "document_count": 1,
        "chunk_count": 1,
    }
    assert versions.status_code == 200
    assert versions.json()["items"][0]["dataset_version"] == "2.0.0"
    assert version_detail.json()["documents"][0]["document_key"] == "support-hours"
    assert detail.status_code == 200
    assert detail.json()["chunks"][0]["stable_chunk_key"] == "support-hours:horarios:1"
    assert missing_run.status_code == 404
    assert missing_run.json()["error"]["code"] == "RAG_RUN_NOT_FOUND"


@pytest.mark.postgres
def test_handoff_queue_claim_messages_notes_return_and_privacy(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    _bootstrap(postgres_engine)
    second_agent_id = _bootstrap(postgres_engine, "agent@topmed.local", "SUPPORT_AGENT")
    embedding_factory = MagicMock()
    llm_factory = MagicMock()
    monkeypatch.setattr("app.api.configured_embedding_provider", embedding_factory)
    monkeypatch.setattr("app.api.DeepSeekProvider", llm_factory)
    application = create_app(postgres_engine, settings=settings)

    with (
        TestClient(application, raise_server_exceptions=False) as admin_client,
        TestClient(application, raise_server_exceptions=False) as agent_client,
    ):
        conversation_id, widget_headers = _widget_conversation(admin_client)
        requested = admin_client.post(
            f"/api/v1/widget/conversations/{conversation_id}/request-human",
            headers=widget_headers,
        )
        repeated = admin_client.post(
            f"/api/v1/widget/conversations/{conversation_id}/request-human",
            headers=widget_headers,
        )
        queued_message_id = str(uuid4())
        queued = admin_client.post(
            f"/api/v1/widget/conversations/{conversation_id}/messages",
            headers=widget_headers,
            json={"content": "Ainda estou aguardando.", "client_message_id": queued_message_id},
        )
        admin_csrf = _login(admin_client)
        agent_csrf = _login(agent_client, "agent@topmed.local")
        queue = admin_client.get("/api/v1/admin/handoffs", headers={"Origin": ORIGIN})

        def claim(client: TestClient, headers: dict[str, str]) -> Any:
            return client.post(
                f"/api/v1/admin/conversations/{conversation_id}/claim",
                headers=headers,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda item: claim(*item),
                    ((admin_client, admin_csrf), (agent_client, agent_csrf)),
                )
            )
        winner = next(response for response in responses if response.status_code == 200)
        loser = next(response for response in responses if response.status_code == 409)
        winning_client = (
            admin_client
            if winner.json()["assigned_agent_id"] != str(second_agent_id)
            else agent_client
        )
        winning_headers = admin_csrf if winning_client is admin_client else agent_csrf
        losing_client = agent_client if winning_client is admin_client else admin_client
        losing_headers = agent_csrf if losing_client is agent_client else admin_csrf
        unassigned_reply = losing_client.post(
            f"/api/v1/admin/conversations/{conversation_id}/messages",
            headers=losing_headers,
            json={
                "content": "Esta resposta não deve ser aceita.",
                "client_message_id": str(uuid4()),
                "visibility": "PUBLIC",
            },
        )
        public_message_id = str(uuid4())
        public = winning_client.post(
            f"/api/v1/admin/conversations/{conversation_id}/messages",
            headers=winning_headers,
            json={
                "content": "Olá, sou uma pessoa da equipe TopMed.",
                "client_message_id": public_message_id,
                "visibility": "PUBLIC",
            },
        )
        repeated_public = winning_client.post(
            f"/api/v1/admin/conversations/{conversation_id}/messages",
            headers=winning_headers,
            json={
                "content": "Olá, sou uma pessoa da equipe TopMed.",
                "client_message_id": public_message_id,
                "visibility": "PUBLIC",
            },
        )
        note = winning_client.post(
            f"/api/v1/admin/conversations/{conversation_id}/messages",
            headers=winning_headers,
            json={
                "content": "Nota privada: confirmar o benefício.",
                "client_message_id": str(uuid4()),
                "visibility": "INTERNAL",
            },
        )
        admin_detail = winning_client.get(
            f"/api/v1/admin/conversations/{conversation_id}",
            headers={"Origin": ORIGIN},
        )
        widget_detail = admin_client.get(
            f"/api/v1/widget/conversations/{conversation_id}",
            headers=widget_headers,
        )
        returned = winning_client.post(
            f"/api/v1/admin/conversations/{conversation_id}/return-to-ai",
            headers=winning_headers,
        )
        requested_again = admin_client.post(
            f"/api/v1/widget/conversations/{conversation_id}/request-human",
            headers=widget_headers,
        )
        reclaimed = winning_client.post(
            f"/api/v1/admin/conversations/{conversation_id}/claim",
            headers=winning_headers,
        )
        closed = winning_client.post(
            f"/api/v1/admin/conversations/{conversation_id}/close",
            headers=winning_headers,
        )
        rejected_after_close = admin_client.post(
            f"/api/v1/widget/conversations/{conversation_id}/messages",
            headers=widget_headers,
            json={"content": "Ainda posso enviar?", "client_message_id": str(uuid4())},
        )

    assert requested.status_code == repeated.status_code == 200
    assert requested.json() == repeated.json()
    assert queued.status_code == 200
    assert queued.json()["delivery_mode"] == "HUMAN_QUEUE"
    embedding_factory.assert_not_called()
    llm_factory.assert_not_called()
    assert queue.status_code == 200
    assert queue.json()["total"] == 1
    assert queue.json()["items"][0]["latest_customer_message"] == "Ainda estou aguardando."
    assert loser.json()["error"]["code"] == "HANDOFF_ALREADY_CLAIMED"
    assert unassigned_reply.status_code == 403
    assert unassigned_reply.json()["error"]["code"] == "HANDOFF_NOT_ASSIGNED"
    assert public.status_code == 200, public.text
    assert repeated_public.json() == public.json()
    assert public.json()["sender_type"] == "HUMAN"
    assert note.status_code == 200
    assert note.json()["visibility"] == "INTERNAL"
    assert admin_detail.status_code == 200
    assert {message["visibility"] for message in admin_detail.json()["messages"]} == {
        "PUBLIC",
        "INTERNAL",
    }
    widget_contents = [message["content"] for message in widget_detail.json()["messages"]]
    assert "Olá, sou uma pessoa da equipe TopMed." in widget_contents
    assert "Nota privada: confirmar o benefício." not in widget_contents
    assert returned.status_code == 200
    assert returned.json()["state"] == "RETURNED_TO_AI"
    assert requested_again.status_code == reclaimed.status_code == closed.status_code == 200
    assert closed.json()["state"] == "CLOSED"
    assert rejected_after_close.status_code == 409
    assert rejected_after_close.json()["error"]["code"] == "CONVERSATION_CLOSED"

    principal = authenticate_widget_session(
        postgres_engine,
        settings,
        token=widget_headers["Authorization"].removeprefix("Bearer "),
        origin=ORIGIN,
    )
    public_events = load_events(
        postgres_engine,
        principal,
        UUID(conversation_id),
        after_id=0,
        limit=100,
    )
    admin_events = load_admin_events(postgres_engine, after_id=0, limit=100)
    assert "note.created" not in {event.event_type for event in public_events}
    assert "note.created" in {event.event_type for event in admin_events}
    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(RagRun)) == 0
        assert session.scalar(select(func.count()).select_from(HandoffEvent)) == 9
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 10
        internal = session.scalar(select(Message).where(Message.visibility == "INTERNAL"))
        assert internal is not None
        event_id = session.scalar(select(HandoffEvent.id).limit(1))
        with pytest.raises(DBAPIError, match="append-only"), session.begin_nested():
            session.execute(
                text("UPDATE handoff_events SET event_type = 'RETURN' WHERE id = :id"),
                {"id": event_id},
            )


@pytest.mark.postgres
def test_conflict_answer_requests_handoff_and_return_resumes_future_ai(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    _seed_active_version(postgres_engine)
    _bootstrap(postgres_engine)
    answers = iter((_answer("CONFLICTING_EVIDENCE"), _answer()))
    conversation_contexts: list[tuple[str, ...]] = []

    def answer(*_args: Any, **kwargs: Any) -> AnswerExecution:
        conversation_contexts.append(kwargs["conversation_context"])
        return AnswerExecution(next(answers), {"source": "test"})

    monkeypatch.setattr("app.conversations.answer_knowledge_with_trace", answer)
    application = create_app(
        postgres_engine,
        StubEmbeddingProvider(),
        StubLLMProvider(),
        settings,
    )
    with TestClient(application, raise_server_exceptions=False) as client:
        conversation_id, widget_headers = _widget_conversation(client)
        first = client.post(
            f"/api/v1/widget/conversations/{conversation_id}/messages",
            headers=widget_headers,
            json={"content": "Há conflito?", "client_message_id": str(uuid4())},
        )
        state = client.get(
            f"/api/v1/widget/conversations/{conversation_id}", headers=widget_headers
        )
        csrf = _login(client)
        rag_detail = client.get(f"/api/v1/admin/rag-runs/{first.json()['rag_run_id']}")
        claimed = client.post(f"/api/v1/admin/conversations/{conversation_id}/claim", headers=csrf)
        note = client.post(
            f"/api/v1/admin/conversations/{conversation_id}/messages",
            headers=csrf,
            json={
                "content": "Nota interna que não pode chegar ao modelo.",
                "client_message_id": str(uuid4()),
                "visibility": "INTERNAL",
            },
        )
        returned = client.post(
            f"/api/v1/admin/conversations/{conversation_id}/return-to-ai", headers=csrf
        )
        second = client.post(
            f"/api/v1/widget/conversations/{conversation_id}/messages",
            headers=widget_headers,
            json={"content": "E agora?", "client_message_id": str(uuid4())},
        )

    assert first.status_code == 200
    assert first.json()["delivery_mode"] == "AI"
    assert state.json()["state"] == "HUMAN_REQUESTED"
    assert rag_detail.status_code == 200
    assert rag_detail.json()["trace"] == {"source": "test"}
    assert rag_detail.json()["conversation_context"] == []
    assert claimed.status_code == note.status_code == returned.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "ANSWERABLE"
    assert conversation_contexts == [(), ("Há conflito?",)]
    with Session(postgres_engine) as session:
        conversation = session.get(Conversation, UUID(conversation_id))
        assert conversation is not None
        assert conversation.state == "AI_ACTIVE"
        assert conversation.handoff_reason == "CONFLICTING_EVIDENCE"
        assert conversation.priority == "HIGH"
        assert session.scalar(select(func.count()).select_from(RagRun)) == 2
        assert all(
            run.embedding_version == StubEmbeddingProvider.model_version
            for run in session.scalars(select(RagRun)).all()
        )
        assert session.scalar(select(func.count()).select_from(HandoffEvent)) == 4


@pytest.mark.postgres
def test_customer_handoff_suppresses_an_in_flight_ai_delivery(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    _seed_active_version(postgres_engine)
    generation_started = Event()
    release_generation = Event()

    def delayed_answer(*_args: Any, **_kwargs: Any) -> AnswerExecution:
        generation_started.set()
        assert release_generation.wait(timeout=5)
        return AnswerExecution(_answer(), {"source": "test"})

    monkeypatch.setattr("app.conversations.answer_knowledge_with_trace", delayed_answer)
    application = create_app(
        postgres_engine,
        StubEmbeddingProvider(),
        StubLLMProvider(),
        settings,
    )
    with (
        TestClient(application, raise_server_exceptions=False) as message_client,
        TestClient(application, raise_server_exceptions=False) as control_client,
    ):
        conversation_id, widget_headers = _widget_conversation(message_client)

        def send_message() -> Any:
            return message_client.post(
                f"/api/v1/widget/conversations/{conversation_id}/messages",
                headers=widget_headers,
                json={"content": "Pergunta em andamento", "client_message_id": str(uuid4())},
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(send_message)
            assert generation_started.wait(timeout=5)
            handoff = control_client.post(
                f"/api/v1/widget/conversations/{conversation_id}/request-human",
                headers=widget_headers,
            )
            release_generation.set()
            response = pending.result(timeout=5)

    assert handoff.status_code == 200
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AI_NOT_IN_CONTROL"
    with Session(postgres_engine) as session:
        run = session.scalar(select(RagRun))
        assert run is not None
        assert run.status == "FAILED"
        assert run.error_code == "AI_CONTROL_LOST"
        assert (
            session.scalar(
                select(func.count()).select_from(Message).where(Message.sender_type == "AI")
            )
            == 0
        )
