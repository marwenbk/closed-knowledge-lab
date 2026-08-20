from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.admin_auth import ADMIN_CSRF_COOKIE, bootstrap_admin
from app.config import Settings
from app.main import create_app
from app.models import (
    AdminUserRole,
    Conversation,
    Feedback,
    KnowledgeBaseVersion,
    Message,
    RagRun,
    WidgetSession,
)
from app.oversight import create_feedback, list_audit_events, list_feedback
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

ORIGIN = "http://localhost:3000"
PASSWORD = "correct horse battery staple"


def _seed_run(engine: Engine) -> UUID:
    now = datetime.now(UTC)
    widget_id = uuid4()
    conversation_id = uuid4()
    user_message_id = uuid4()
    ai_message_id = uuid4()
    kb_id = uuid4()
    run_id = uuid4()
    with Session(engine) as session, session.begin():
        session.add(
            WidgetSession(
                id=widget_id,
                anonymous_subject="feedback-subject",
                origin=ORIGIN,
                locale="pt-BR",
                expires_at=now + timedelta(hours=1),
                last_seen_at=now,
            )
        )
        session.flush()
        session.add(
            Conversation(
                id=conversation_id,
                widget_session_id=widget_id,
                state="AI_ACTIVE",
                last_message_at=now,
            )
        )
        session.flush()
        session.add(
            KnowledgeBaseVersion(
                id=kb_id,
                dataset_id="topmed-demo",
                dataset_version="2.0.0",
                generator_version="1.0.0",
                language="pt-BR",
                seed_checksum="a" * 64,
                template_checksum="b" * 64,
                manifest_checksum="c" * 64,
                status="ACTIVE",
                activated_at=now,
            )
        )
        session.flush()
        session.add_all(
            (
                Message(
                    id=user_message_id,
                    conversation_id=conversation_id,
                    client_message_id=uuid4(),
                    sender_type="CUSTOMER",
                    content="Pergunta",
                    visibility="PUBLIC",
                    status="PERSISTED",
                    citations_json=[],
                ),
                Message(
                    id=ai_message_id,
                    conversation_id=conversation_id,
                    sender_type="AI",
                    content="Resposta",
                    visibility="PUBLIC",
                    status="DELIVERED",
                    review_status="NONE",
                    review_regeneration_count=0,
                    reply_to_message_id=user_message_id,
                    citations_json=[],
                    delivered_at=now,
                ),
            )
        )
        session.flush()
        session.add(
            RagRun(
                id=run_id,
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                assistant_message_id=ai_message_id,
                kb_version_id=kb_id,
                original_query="Pergunta",
                retrieval_query="Pergunta",
                conversation_context_json=[],
                status="COMPLETED",
                answerability_status="ANSWERABLE",
                verification_status="VERIFIED",
                model_provider="deepseek",
                model_name="deepseek-v4-flash",
                model_version="deepseek-v4-flash",
                prompt_version="1.0.0",
                embedding_version="614241f622f53c4eeff9890bdc4f31cfecc418b3",
                settings_version="1.0.0",
                regenerated=False,
                latency_ms=1.0,
                completed_at=now,
            )
        )
    return run_id


@pytest.mark.postgres
def test_feedback_and_audit_history_are_append_only(postgres_engine: Engine) -> None:
    run_id = _seed_run(postgres_engine)
    admin = bootstrap_admin(
        postgres_engine,
        email="auditor@topmed.test",
        display_name="Auditor",
        password=PASSWORD,
    )
    created = create_feedback(
        postgres_engine,
        rag_run_id=run_id,
        category="GROUNDING_FAILURE",
        note="A citação não cobre a frase final.",
        actor_id=admin.user_id,
        request_id=uuid4(),
    )
    feedback, feedback_total = list_feedback(
        postgres_engine,
        rag_run_id=run_id,
        category="GROUNDING_FAILURE",
        offset=0,
        limit=10,
    )
    events, event_total = list_audit_events(
        postgres_engine,
        event_type="feedback.created",
        actor_type="ADMIN",
        resource_type="feedback",
        resource_id=str(created["id"]),
        created_from=None,
        created_to=None,
        offset=0,
        limit=10,
    )
    assert feedback_total == 1 and feedback[0]["note"] == "A citação não cobre a frase final."
    assert event_total == 1 and events[0]["after"]["rag_run_id"] == str(run_id)
    with Session(postgres_engine) as session, session.begin():
        row = session.get(Feedback, created["id"])
        assert row is not None
        row.note = "mutated"
        with pytest.raises(DBAPIError, match="append-only"):
            session.flush()


@pytest.mark.postgres
def test_auditor_reads_history_but_cannot_create_feedback(postgres_engine: Engine) -> None:
    run_id = _seed_run(postgres_engine)
    auditor = bootstrap_admin(
        postgres_engine,
        email="readonly-auditor@topmed.test",
        display_name="Readonly Auditor",
        password=PASSWORD,
    )
    with Session(postgres_engine) as session, session.begin():
        session.execute(delete(AdminUserRole).where(AdminUserRole.user_id == auditor.user_id))
        session.add(AdminUserRole(user_id=auditor.user_id, role="AUDITOR"))
    settings = Settings(
        _env_file=None,
        widget_token_secret="test-widget-token-secret-at-least-32-characters",
        admin_allowed_origins=ORIGIN,
    )
    with TestClient(
        create_app(postgres_engine, settings=settings),
        raise_server_exceptions=False,
    ) as client:
        login = client.post(
            "/api/v1/admin/auth/login",
            headers={"Origin": ORIGIN},
            json={"email": "readonly-auditor@topmed.test", "password": PASSWORD},
        )
        csrf = client.cookies.get(ADMIN_CSRF_COOKIE)
        assert login.status_code == 200 and csrf
        audit = client.get("/api/v1/admin/audit-events")
        denied = client.post(
            "/api/v1/admin/feedback",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
            json={"rag_run_id": str(run_id), "category": "CORRECT", "note": None},
        )
    assert audit.status_code == 200
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ADMIN_PERMISSION_DENIED"
