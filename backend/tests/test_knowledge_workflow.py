from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import pytest
from app.admin_auth import ADMIN_CSRF_COOKIE, bootstrap_admin
from app.config import Settings
from app.embeddings import (
    EmbeddingError,
    OnnxE5EmbeddingProvider,
    embed_knowledge_base,
    ensure_model_artifacts,
)
from app.kb import activate_knowledge_base, import_knowledge_base
from app.knowledge_workflow import (
    KnowledgeWorkflowError,
    create_draft,
    evaluate_version,
    get_document,
    index_version,
    publish_version,
    update_document,
    validate_version,
)
from app.main import create_app
from app.models import AdminUserRole, AuditEvent, Document, EvaluationRun
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "knowledge_base" / "manifest.json"
ORIGIN = "http://localhost:3000"


class FakeEmbeddingProvider:
    model_id = "intfloat/multilingual-e5-small"
    model_version = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    dimensions = 384

    def _vector(self, value: str) -> list[float]:
        digest = hashlib.sha256(value.encode()).digest()
        values = [(byte - 127.5) / 127.5 for byte in digest * 12]
        norm = math.sqrt(sum(item * item for item in values))
        return [item / norm for item in values]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(f"passage: {text}") for text in texts]

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(f"query: {text}") for text in texts]


def _prepare_active_knowledge(
    engine: Engine,
    provider: FakeEmbeddingProvider,
    settings: Settings,
) -> None:
    import_knowledge_base(engine, MANIFEST_PATH)
    embed_knowledge_base(
        engine,
        provider,
        batch_size=settings.embedding_batch_size,
        dataset_id=settings.expected_dataset_id,
        dataset_version=settings.expected_dataset_version,
    )
    activate_knowledge_base(
        engine,
        dataset_id=settings.expected_dataset_id,
        dataset_version=settings.expected_dataset_version,
        expected_embedding_model=provider.model_id,
        expected_embedding_version=provider.model_version,
        expected_embedding_dimensions=provider.dimensions,
    )


@pytest.mark.postgres
def test_governed_draft_validation_publication_and_rollback(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None)
    provider = FakeEmbeddingProvider()
    actor = bootstrap_admin(
        postgres_engine,
        email="publisher@topmed.local",
        display_name="Publisher",
        password="correct horse battery staple",
    ).user_id
    imported = import_knowledge_base(postgres_engine, MANIFEST_PATH)
    embed_knowledge_base(
        postgres_engine,
        provider,
        batch_size=settings.embedding_batch_size,
        dataset_id=settings.expected_dataset_id,
        dataset_version=settings.expected_dataset_version,
    )
    activate_knowledge_base(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        dataset_version=settings.expected_dataset_version,
        expected_embedding_model=provider.model_id,
        expected_embedding_version=provider.model_version,
        expected_embedding_dimensions=provider.dimensions,
    )

    draft = create_draft(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        dataset_version="3.0.1",
        actor_id=actor,
        request_id=uuid4(),
    )
    assert draft["source_version_id"] == imported.kb_version_id
    assert draft["document_count"] == 15
    assert draft["embedded_chunk_count"] == 0
    initial_validation = validate_version(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        actor_id=actor,
        request_id=uuid4(),
    )
    assert initial_validation["passed"] is True

    with Session(postgres_engine) as session:
        family = session.scalar(
            select(Document).where(
                Document.kb_version_id == draft["id"],
                Document.document_key == "family-members",
            )
        )
        assert family is not None
    detail = get_document(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        document_id=family.id,
    )
    assert "PLAN_FAMILY_MAX_DEPENDENTS" in detail["dependencies"]["fact_ids"]
    revised = update_document(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        document_id=family.id,
        content=f"{detail['content_markdown'].rstrip()}\n\nNota editorial aprovada.\n",
        actor_id=actor,
        request_id=uuid4(),
    )
    assert revised["revision_number"] == 2
    assert [item["revision_number"] for item in revised["revisions"]] == [2, 1]
    with pytest.raises(KnowledgeWorkflowError, match="validation is required"):
        index_version(
            postgres_engine,
            provider,
            settings,
            dataset_id=settings.expected_dataset_id,
            version_id=draft["id"],
            actor_id=actor,
            request_id=uuid4(),
        )

    validation = validate_version(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        actor_id=actor,
        request_id=uuid4(),
    )
    assert validation["passed"] is True
    indexed = index_version(
        postgres_engine,
        provider,
        settings,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        actor_id=actor,
        request_id=uuid4(),
    )
    assert indexed["embedded_chunk_count"] == indexed["chunk_count"]
    with pytest.raises(KnowledgeWorkflowError, match="passing evaluation"):
        publish_version(
            postgres_engine,
            settings,
            dataset_id=settings.expected_dataset_id,
            version_id=draft["id"],
            actor_id=actor,
            request_id=uuid4(),
        )

    passing_report = {
        "passed": True,
        "mode": "retrieval",
        "retrieval": {"evaluated_cases": 63, "status": "passed"},
    }

    def edit_during_evaluation(*_args: object, **_kwargs: object) -> dict[str, object]:
        update_document(
            postgres_engine,
            dataset_id=settings.expected_dataset_id,
            version_id=draft["id"],
            document_id=family.id,
            content=f"{revised['content_markdown'].rstrip()}\n\nConcurrent change.\n",
            actor_id=actor,
            request_id=uuid4(),
        )
        return passing_report

    monkeypatch.setattr("app.knowledge_workflow.run_evaluation", edit_during_evaluation)
    stale_report = evaluate_version(
        postgres_engine,
        provider,
        settings,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        actor_id=actor,
        request_id=uuid4(),
    )
    assert stale_report == {
        "passed": False,
        "error": {"code": "DRAFT_CHANGED_DURING_EVALUATION"},
    }
    validate_version(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        actor_id=actor,
        request_id=uuid4(),
    )
    index_version(
        postgres_engine,
        provider,
        settings,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        actor_id=actor,
        request_id=uuid4(),
    )
    monkeypatch.setattr(
        "app.knowledge_workflow.run_evaluation",
        lambda *_args, **_kwargs: passing_report,
    )
    report = evaluate_version(
        postgres_engine,
        provider,
        settings,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        actor_id=actor,
        request_id=uuid4(),
    )
    assert report["passed"] is True
    active = publish_version(
        postgres_engine,
        settings,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        actor_id=actor,
        request_id=uuid4(),
    )
    assert active["status"] == "ACTIVE"

    with pytest.raises(DBAPIError), Session(postgres_engine) as session, session.begin():
        session.execute(
            text("UPDATE documents SET title = 'tampered' WHERE kb_version_id = :version_id"),
            {"version_id": draft["id"]},
        )
    with pytest.raises(DBAPIError), Session(postgres_engine) as session, session.begin():
        session.execute(
            text("UPDATE knowledge_base_versions SET manifest_checksum = :checksum WHERE id = :id"),
            {"checksum": "f" * 64, "id": draft["id"]},
        )

    restored = publish_version(
        postgres_engine,
        settings,
        dataset_id=settings.expected_dataset_id,
        version_id=imported.kb_version_id,
        actor_id=actor,
        request_id=uuid4(),
        rollback=True,
    )
    assert restored["status"] == "ACTIVE"
    with Session(postgres_engine) as session:
        assert list(
            session.scalars(
                select(EvaluationRun.status)
                .where(EvaluationRun.kb_version_id == draft["id"])
                .order_by(EvaluationRun.started_at)
            )
        ) == ["FAILED", "PASSED"]
        event_types = set(
            session.scalars(
                select(AuditEvent.event_type).where(
                    AuditEvent.resource_id.in_((str(draft["id"]), str(imported.kb_version_id)))
                )
            )
        )
    assert {
        "knowledge_base.draft_created",
        "knowledge_base.document_revised",
        "knowledge_base.validated",
        "knowledge_base.embedded",
        "knowledge_base.evaluated",
        "knowledge_base.activated",
        "knowledge_base.rolled_back",
    }.issubset(event_types)


@pytest.mark.postgres
def test_validation_rejects_archived_conflicting_policy(postgres_engine: Engine) -> None:
    settings = Settings(_env_file=None)
    provider = FakeEmbeddingProvider()
    actor = bootstrap_admin(
        postgres_engine,
        email="editor@topmed.local",
        display_name="Editor",
        password="correct horse battery staple",
    ).user_id
    import_knowledge_base(postgres_engine, MANIFEST_PATH)
    embed_knowledge_base(
        postgres_engine,
        provider,
        batch_size=settings.embedding_batch_size,
        dataset_id=settings.expected_dataset_id,
        dataset_version=settings.expected_dataset_version,
    )
    activate_knowledge_base(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        dataset_version=settings.expected_dataset_version,
        expected_embedding_model=provider.model_id,
        expected_embedding_version=provider.model_version,
        expected_embedding_dimensions=provider.dimensions,
    )
    draft = create_draft(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        dataset_version="3.0.1",
        actor_id=actor,
        request_id=uuid4(),
    )
    with Session(postgres_engine) as session:
        employer = session.scalar(
            select(Document).where(
                Document.kb_version_id == draft["id"],
                Document.document_key == "employer-plans",
            )
        )
        assert employer is not None
    detail = get_document(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        document_id=employer.id,
    )
    update_document(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        document_id=employer.id,
        content=(
            f"{detail['content_markdown'].rstrip()}\n\n"
            "The employer Gold tier maps to the Premium plan.\n"
        ),
        actor_id=actor,
        request_id=uuid4(),
    )
    report = validate_version(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        actor_id=actor,
        request_id=uuid4(),
    )
    assert report["passed"] is False
    assert "conflict_gold_mapping" in report["conflict_fixture_ids"]


@pytest.mark.postgres
def test_knowledge_editor_can_draft_but_cannot_publish(postgres_engine: Engine) -> None:
    settings = Settings(
        _env_file=None,
        widget_token_secret="test-widget-token-secret-at-least-32-characters",
        admin_allowed_origins=ORIGIN,
    )
    provider = FakeEmbeddingProvider()
    _prepare_active_knowledge(postgres_engine, provider, settings)
    editor = bootstrap_admin(
        postgres_engine,
        email="knowledge@topmed.local",
        display_name="Knowledge Editor",
        password="correct horse battery staple",
    )
    with Session(postgres_engine) as session, session.begin():
        session.execute(delete(AdminUserRole).where(AdminUserRole.user_id == editor.user_id))
        session.add(AdminUserRole(user_id=editor.user_id, role="KNOWLEDGE_EDITOR"))
    application = create_app(postgres_engine, provider, settings=settings)

    with TestClient(application, raise_server_exceptions=False) as client:
        login = client.post(
            "/api/v1/admin/auth/login",
            headers={"Origin": ORIGIN},
            json={
                "email": "knowledge@topmed.local",
                "password": "correct horse battery staple",
            },
        )
        csrf = client.cookies.get(ADMIN_CSRF_COOKIE)
        assert login.status_code == 200 and csrf
        headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
        created = client.post(
            "/api/v1/admin/knowledge/versions",
            headers=headers,
            json={"dataset_version": "3.0.1"},
        )
        assert created.status_code == 201, created.text
        versions = client.get("/api/v1/admin/knowledge/versions")
        denied = client.post(
            f"/api/v1/admin/knowledge/versions/{created.json()['id']}/actions",
            headers=headers,
            json={"action": "ACTIVATE"},
        )

    assert versions.status_code == 200
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ADMIN_PERMISSION_DENIED"


@pytest.mark.model
@pytest.mark.postgres
def test_draft_passes_the_real_retrieval_publication_gate(postgres_engine: Engine) -> None:
    settings = Settings(_env_file=None)
    try:
        ensure_model_artifacts(settings)
    except EmbeddingError as exc:
        if "is missing" in str(exc):
            if os.environ.get("TOPMED_REQUIRE_MODEL_TESTS") == "1":
                pytest.fail(f"Pinned embedding model is required but unavailable: {exc}")
            pytest.skip("Pinned embedding model has not been downloaded by backend setup")
        raise
    provider = OnnxE5EmbeddingProvider(settings)
    actor = bootstrap_admin(
        postgres_engine,
        email="evaluator@topmed.local",
        display_name="Evaluator",
        password="correct horse battery staple",
    ).user_id
    import_knowledge_base(postgres_engine, MANIFEST_PATH)
    embed_knowledge_base(
        postgres_engine,
        provider,
        batch_size=settings.embedding_batch_size,
        dataset_id=settings.expected_dataset_id,
        dataset_version=settings.expected_dataset_version,
    )
    activate_knowledge_base(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        dataset_version=settings.expected_dataset_version,
        expected_embedding_model=provider.model_id,
        expected_embedding_version=provider.model_version,
        expected_embedding_dimensions=provider.dimensions,
    )
    draft = create_draft(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        dataset_version="3.0.1",
        actor_id=actor,
        request_id=uuid4(),
    )
    validation = validate_version(
        postgres_engine,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        actor_id=actor,
        request_id=uuid4(),
    )
    assert validation["passed"] is True
    index_version(
        postgres_engine,
        provider,
        settings,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        actor_id=actor,
        request_id=uuid4(),
    )
    report = evaluate_version(
        postgres_engine,
        provider,
        settings,
        dataset_id=settings.expected_dataset_id,
        version_id=draft["id"],
        actor_id=actor,
        request_id=uuid4(),
    )

    assert report["passed"] is True
    assert report["dataset_version"] == "3.0.1"
    assert report["retrieval"]["evaluated_cases"] == 63
