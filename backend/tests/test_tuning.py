from __future__ import annotations

import math
from collections.abc import Sequence
from uuid import uuid4

import pytest
from app.admin_auth import bootstrap_admin
from app.config import PROJECT_ROOT, Settings
from app.embeddings import embed_knowledge_base
from app.kb import activate_knowledge_base, import_knowledge_base
from app.models import AuditEvent, PromptVersion, SettingsVersion
from app.tuning import (
    PromptBundle,
    RetrievalTuning,
    activate_runtime_version,
    create_prompt_draft,
    create_settings_draft,
    evaluate_prompt_version,
    evaluate_settings_version,
    load_runtime_snapshot,
    prompt_checksum,
    settings_checksum,
    update_prompt_version,
    update_settings_version,
)
from sqlalchemy import Engine, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


class FakeEmbeddingProvider:
    model_id = "intfloat/multilingual-e5-small"
    model_version = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    dimensions = 384

    def _vectors(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for index, _text in enumerate(texts):
            vector = [0.0] * self.dimensions
            vector[index % self.dimensions] = 1.0
            norm = math.sqrt(sum(value * value for value in vector))
            vectors.append([value / norm for value in vector])
        return vectors

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._vectors(texts)

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return self._vectors(texts)


def _passing_report(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {
        "passed": True,
        "retrieval": {
            "status": "passed",
            "evaluated_cases": 63,
            "passed_cases": 63,
            "source_recall_at_k": 1.0,
            "fact_recall_at_k": 1.0,
        },
        "answering": {
            "status": "passed",
            "evaluated_cases": 100,
            "passed_cases": 100,
        },
    }


def _active_knowledge(engine: Engine, provider: FakeEmbeddingProvider, settings: Settings) -> None:
    import_knowledge_base(engine, PROJECT_ROOT / "knowledge_base/manifest.json")
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
def test_prompt_and_settings_versions_are_evaluated_immutable_and_audited(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings()
    provider = FakeEmbeddingProvider()
    _active_knowledge(postgres_engine, provider, settings)
    admin = bootstrap_admin(
        postgres_engine,
        email="tuning-admin@topmed.test",
        display_name="Tuning Admin",
        password="tuning-admin-password-42",
    )
    request_id = uuid4()
    monkeypatch.setattr("app.evaluation.run_evaluation", _passing_report)

    baseline = load_runtime_snapshot(postgres_engine, settings)
    assert baseline.prompt_version == "2.0.0"
    assert baseline.settings_version == "1.0.0"
    assert baseline.prompt_checksum == prompt_checksum(baseline.prompts)
    assert baseline.settings_checksum == settings_checksum(baseline.retrieval)

    prompt = create_prompt_draft(
        postgres_engine,
        version="2.0.1",
        actor_id=admin.user_id,
        request_id=request_id,
    )
    revised_prompts = PromptBundle.model_validate(
        {
            **prompt["prompts"],
            "generation_prompt": str(prompt["prompts"]["generation_prompt"])
            + " Preserve concise responses.",
        }
    )
    update_prompt_version(
        postgres_engine,
        prompt["id"],
        revised_prompts,
        actor_id=admin.user_id,
        request_id=request_id,
    )
    evaluated_prompt = evaluate_prompt_version(
        postgres_engine,
        provider,
        object(),
        settings,
        prompt["id"],
        actor_id=admin.user_id,
        request_id=request_id,
    )
    assert evaluated_prompt["status"] == "EVALUATED"
    assert evaluated_prompt["evaluation"]["baseline_run_id"] is not None
    activate_runtime_version(
        postgres_engine,
        settings,
        prompt["id"],
        kind="PROMPT",
        rollback=False,
        actor_id=admin.user_id,
        request_id=request_id,
    )

    settings_draft = create_settings_draft(
        postgres_engine,
        version="1.0.1",
        actor_id=admin.user_id,
        request_id=request_id,
    )
    retrieval = RetrievalTuning.model_validate({**settings_draft["settings"], "rrf_k": 61})
    update_settings_version(
        postgres_engine,
        settings_draft["id"],
        retrieval,
        actor_id=admin.user_id,
        request_id=request_id,
    )
    evaluated_settings = evaluate_settings_version(
        postgres_engine,
        provider,
        settings,
        settings_draft["id"],
        actor_id=admin.user_id,
        request_id=request_id,
    )
    assert evaluated_settings["status"] == "EVALUATED"
    activate_runtime_version(
        postgres_engine,
        settings,
        settings_draft["id"],
        kind="SETTINGS",
        rollback=False,
        actor_id=admin.user_id,
        request_id=request_id,
    )

    current = load_runtime_snapshot(postgres_engine, settings)
    assert current.prompt_version == "2.0.1"
    assert current.settings_version == "1.0.1"
    assert current.retrieval.rrf_k == 61
    with Session(postgres_engine) as session, session.begin():
        active_prompt = session.scalar(
            select(PromptVersion).where(PromptVersion.status == "ACTIVE")
        )
        assert active_prompt is not None
        active_prompt.generation_prompt += " forbidden mutation"
        with pytest.raises(DBAPIError, match="immutable"):
            session.flush()

    with Session(postgres_engine) as session:
        event_types = set(session.scalars(select(AuditEvent.event_type)))
        assert {
            "prompt_version.created",
            "prompt_version.updated",
            "prompt_version.activated",
            "settings_version.created",
            "settings_version.updated",
            "settings_version.activated",
            "evaluation.completed",
        }.issubset(event_types)


@pytest.mark.postgres
def test_editing_an_evaluated_draft_invalidates_its_gate(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings()
    provider = FakeEmbeddingProvider()
    _active_knowledge(postgres_engine, provider, settings)
    admin = bootstrap_admin(
        postgres_engine,
        email="tuning-editor@topmed.test",
        display_name="Tuning Editor",
        password="tuning-editor-password-42",
    )
    monkeypatch.setattr("app.evaluation.run_evaluation", _passing_report)
    request_id = uuid4()
    draft = create_settings_draft(
        postgres_engine, version="1.0.2", actor_id=admin.user_id, request_id=request_id
    )
    evaluate_settings_version(
        postgres_engine,
        provider,
        settings,
        draft["id"],
        actor_id=admin.user_id,
        request_id=request_id,
    )
    changed = RetrievalTuning.model_validate({**draft["settings"], "rrf_k": 62})
    result = update_settings_version(
        postgres_engine,
        draft["id"],
        changed,
        actor_id=admin.user_id,
        request_id=request_id,
    )
    assert result["status"] == "DRAFT"
    with Session(postgres_engine) as session:
        version = session.get(SettingsVersion, draft["id"])
        assert version is not None and version.content_checksum == settings_checksum(changed)
