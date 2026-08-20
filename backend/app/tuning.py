from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    AuditEvent,
    EvaluationRun,
    KnowledgeBaseVersion,
    PromptVersion,
    SettingsVersion,
)

SEMVER_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
TUNING_LOCK_KEY = "topmed:tuning-evaluation"


class TuningError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class PromptBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    answerability_prompt: str = Field(min_length=100, max_length=12_000)
    generation_prompt: str = Field(min_length=100, max_length=12_000)
    verification_prompt: str = Field(min_length=100, max_length=12_000)

    @field_validator("answerability_prompt", "generation_prompt", "verification_prompt")
    @classmethod
    def reject_unsafe_controls(cls, value: str) -> str:
        if any(
            unicodedata.category(character) == "Cc" and character not in {"\n", "\t"}
            for character in value
        ):
            raise ValueError("prompt contains unsupported control characters")
        return value


class RetrievalTuning(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    vector_top_k: int = Field(gt=0, le=100)
    lexical_top_k: int = Field(gt=0, le=100)
    trigram_top_k: int = Field(gt=0, le=100)
    final_context_k: int = Field(gt=0, le=100)
    rrf_k: int = Field(gt=0, le=10_000)
    min_vector_similarity: float = Field(ge=0, le=1)
    trigram_min_similarity: float = Field(ge=0, le=1)
    trigram_fallback_enabled: bool
    second_hop_enabled: bool


@dataclass(frozen=True)
class RuntimeSnapshot:
    prompt_id: UUID
    prompt_version: str
    prompt_checksum: str
    prompts: PromptBundle
    settings_id: UUID
    settings_version: str
    settings_checksum: str
    retrieval: RetrievalTuning
    effective_settings: Settings


def _now() -> datetime:
    return datetime.now(UTC)


def _checksum(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def prompt_checksum(bundle: PromptBundle) -> str:
    return _checksum(bundle.model_dump(mode="json"))


def settings_checksum(settings: RetrievalTuning) -> str:
    return _checksum(settings.model_dump(mode="json"))


def _prompt_bundle(version: PromptVersion) -> PromptBundle:
    return PromptBundle(
        answerability_prompt=version.answerability_prompt,
        generation_prompt=version.generation_prompt,
        verification_prompt=version.verification_prompt,
    )


def _retrieval_tuning(version: SettingsVersion) -> RetrievalTuning:
    return RetrievalTuning.model_validate(version.settings_json)


def runtime_snapshot(session: Session, base: Settings, *, lock: bool = False) -> RuntimeSnapshot:
    prompt_statement = select(PromptVersion).where(PromptVersion.status == "ACTIVE")
    settings_statement = select(SettingsVersion).where(SettingsVersion.status == "ACTIVE")
    if lock:
        prompt_statement = prompt_statement.with_for_update()
        settings_statement = settings_statement.with_for_update()
    prompt = session.scalar(prompt_statement)
    retrieval_settings = session.scalar(settings_statement)
    if prompt is None or retrieval_settings is None:
        raise TuningError(
            503, "RUNTIME_CONFIGURATION_NOT_READY", "Runtime configuration is missing"
        )
    prompts = _prompt_bundle(prompt)
    retrieval = _retrieval_tuning(retrieval_settings)
    if prompt_checksum(prompts) != prompt.content_checksum:
        raise TuningError(503, "PROMPT_CHECKSUM_INVALID", "The active prompt checksum is invalid")
    if settings_checksum(retrieval) != retrieval_settings.content_checksum:
        raise TuningError(
            503, "SETTINGS_CHECKSUM_INVALID", "The active settings checksum is invalid"
        )
    effective = base.model_copy(
        update={
            **retrieval.model_dump(),
            "prompt_version": prompt.version,
            "settings_version": retrieval_settings.version,
        }
    )
    return RuntimeSnapshot(
        prompt_id=prompt.id,
        prompt_version=prompt.version,
        prompt_checksum=prompt.content_checksum,
        prompts=prompts,
        settings_id=retrieval_settings.id,
        settings_version=retrieval_settings.version,
        settings_checksum=retrieval_settings.content_checksum,
        retrieval=retrieval,
        effective_settings=effective,
    )


def load_runtime_snapshot(engine: Engine, base: Settings) -> RuntimeSnapshot:
    with Session(engine) as session:
        return runtime_snapshot(session, base)


def runtime_status(engine: Engine) -> dict[str, Any]:
    with Session(engine) as session:
        prompt = session.scalar(select(PromptVersion).where(PromptVersion.status == "ACTIVE"))
        settings_version = session.scalar(
            select(SettingsVersion).where(SettingsVersion.status == "ACTIVE")
        )
        if prompt is None or settings_version is None:
            raise TuningError(
                503, "RUNTIME_CONFIGURATION_NOT_READY", "Runtime configuration is missing"
            )
        prompts = _prompt_bundle(prompt)
        retrieval = _retrieval_tuning(settings_version)
        return {
            "status": (
                "ready"
                if prompt_checksum(prompts) == prompt.content_checksum
                and settings_checksum(retrieval) == settings_version.content_checksum
                and not settings_version.requires_reindex
                else "not_ready"
            ),
            "prompt_version": prompt.version,
            "prompt_checksum": prompt.content_checksum,
            "settings_version": settings_version.version,
            "settings_checksum": settings_version.content_checksum,
            "requires_reindex": settings_version.requires_reindex,
        }


def _audit(
    session: Session,
    *,
    event_type: str,
    resource_type: str,
    resource_id: UUID,
    actor_id: UUID,
    request_id: UUID,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            id=uuid4(),
            event_type=event_type,
            actor_type="ADMIN",
            actor_id=str(actor_id),
            resource_type=resource_type,
            resource_id=str(resource_id),
            request_id=request_id,
            before_json=before,
            after_json=after,
            metadata_json=metadata or {},
        )
    )


def _version_number(value: str) -> str:
    clean = value.strip()
    if not SEMVER_PATTERN.fullmatch(clean):
        raise TuningError(422, "INVALID_VERSION", "Version must use semantic versioning")
    return clean


def _latest_evaluation(
    session: Session,
    *,
    prompt_version: str | None = None,
    settings_version: str | None = None,
) -> EvaluationRun | None:
    statement = select(EvaluationRun)
    if prompt_version is not None:
        statement = statement.where(EvaluationRun.prompt_version == prompt_version)
    if settings_version is not None:
        statement = statement.where(EvaluationRun.settings_version == settings_version)
    return session.scalar(statement.order_by(EvaluationRun.started_at.desc()).limit(1))


def _evaluation_payload(run: EvaluationRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "suite": run.suite,
        "mode": run.mode,
        "status": run.status,
        "baseline_run_id": run.baseline_run_id,
        "metrics": run.metrics_json,
        "error_code": run.error_code,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


def _prompt_payload(session: Session, version: PromptVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "version": version.version,
        "status": version.status,
        "content_checksum": version.content_checksum,
        "source_version_id": version.source_version_id,
        "created_by": version.created_by,
        "activated_by": version.activated_by,
        "created_at": version.created_at,
        "updated_at": version.updated_at,
        "activated_at": version.activated_at,
        "prompts": _prompt_bundle(version).model_dump(mode="json"),
        "evaluation": _evaluation_payload(
            _latest_evaluation(session, prompt_version=version.version)
        ),
    }


def _settings_payload(session: Session, version: SettingsVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "version": version.version,
        "status": version.status,
        "content_checksum": version.content_checksum,
        "requires_reindex": version.requires_reindex,
        "source_version_id": version.source_version_id,
        "created_by": version.created_by,
        "activated_by": version.activated_by,
        "created_at": version.created_at,
        "updated_at": version.updated_at,
        "activated_at": version.activated_at,
        "settings": _retrieval_tuning(version).model_dump(mode="json"),
        "evaluation": _evaluation_payload(
            _latest_evaluation(session, settings_version=version.version)
        ),
    }


def list_prompt_versions(engine: Engine) -> tuple[dict[str, Any], ...]:
    with Session(engine) as session:
        versions = session.scalars(select(PromptVersion).order_by(PromptVersion.created_at.desc()))
        return tuple(_prompt_payload(session, version) for version in versions)


def list_settings_versions(engine: Engine) -> tuple[dict[str, Any], ...]:
    with Session(engine) as session:
        versions = session.scalars(
            select(SettingsVersion).order_by(SettingsVersion.created_at.desc())
        )
        return tuple(_settings_payload(session, version) for version in versions)


def _prompt(session: Session, version_id: UUID, *, lock: bool = False) -> PromptVersion:
    statement = select(PromptVersion).where(PromptVersion.id == version_id)
    version = session.scalar(statement.with_for_update() if lock else statement)
    if version is None:
        raise TuningError(404, "PROMPT_VERSION_NOT_FOUND", "Prompt version was not found")
    return version


def _settings(session: Session, version_id: UUID, *, lock: bool = False) -> SettingsVersion:
    statement = select(SettingsVersion).where(SettingsVersion.id == version_id)
    version = session.scalar(statement.with_for_update() if lock else statement)
    if version is None:
        raise TuningError(404, "SETTINGS_VERSION_NOT_FOUND", "Settings version was not found")
    return version


def get_prompt_version(engine: Engine, version_id: UUID) -> dict[str, Any]:
    with Session(engine) as session:
        return _prompt_payload(session, _prompt(session, version_id))


def get_settings_version(engine: Engine, version_id: UUID) -> dict[str, Any]:
    with Session(engine) as session:
        return _settings_payload(session, _settings(session, version_id))


def create_prompt_draft(
    engine: Engine, *, version: str, actor_id: UUID, request_id: UUID
) -> dict[str, Any]:
    with Session(engine) as session, session.begin():
        active = session.scalar(
            select(PromptVersion).where(PromptVersion.status == "ACTIVE").with_for_update()
        )
        if active is None:
            raise TuningError(409, "ACTIVE_PROMPT_REQUIRED", "An active prompt is required")
        draft = PromptVersion(
            id=uuid4(),
            version=_version_number(version),
            status="DRAFT",
            answerability_prompt=active.answerability_prompt,
            generation_prompt=active.generation_prompt,
            verification_prompt=active.verification_prompt,
            content_checksum=active.content_checksum,
            source_version_id=active.id,
            created_by=actor_id,
        )
        session.add(draft)
        try:
            session.flush()
        except IntegrityError as exc:
            raise TuningError(
                409, "PROMPT_VERSION_EXISTS", "Prompt version already exists"
            ) from exc
        _audit(
            session,
            event_type="prompt_version.created",
            resource_type="prompt_version",
            resource_id=draft.id,
            actor_id=actor_id,
            request_id=request_id,
            before=None,
            after={"version": draft.version, "status": draft.status},
        )
        return _prompt_payload(session, draft)


def create_settings_draft(
    engine: Engine, *, version: str, actor_id: UUID, request_id: UUID
) -> dict[str, Any]:
    with Session(engine) as session, session.begin():
        active = session.scalar(
            select(SettingsVersion).where(SettingsVersion.status == "ACTIVE").with_for_update()
        )
        if active is None:
            raise TuningError(409, "ACTIVE_SETTINGS_REQUIRED", "Active settings are required")
        draft = SettingsVersion(
            id=uuid4(),
            version=_version_number(version),
            status="DRAFT",
            settings_json=dict(active.settings_json),
            content_checksum=active.content_checksum,
            requires_reindex=False,
            source_version_id=active.id,
            created_by=actor_id,
        )
        session.add(draft)
        try:
            session.flush()
        except IntegrityError as exc:
            raise TuningError(
                409, "SETTINGS_VERSION_EXISTS", "Settings version already exists"
            ) from exc
        _audit(
            session,
            event_type="settings_version.created",
            resource_type="settings_version",
            resource_id=draft.id,
            actor_id=actor_id,
            request_id=request_id,
            before=None,
            after={"version": draft.version, "status": draft.status},
        )
        return _settings_payload(session, draft)


def update_prompt_version(
    engine: Engine,
    version_id: UUID,
    bundle: PromptBundle,
    *,
    actor_id: UUID,
    request_id: UUID,
) -> dict[str, Any]:
    with Session(engine) as session, session.begin():
        version = _prompt(session, version_id, lock=True)
        if version.status not in {"DRAFT", "EVALUATED"}:
            raise TuningError(409, "PROMPT_IMMUTABLE", "Only draft prompts can be edited")
        before = {"status": version.status, "checksum": version.content_checksum}
        version.answerability_prompt = bundle.answerability_prompt
        version.generation_prompt = bundle.generation_prompt
        version.verification_prompt = bundle.verification_prompt
        version.content_checksum = prompt_checksum(bundle)
        version.status = "DRAFT"
        version.updated_at = _now()
        _audit(
            session,
            event_type="prompt_version.updated",
            resource_type="prompt_version",
            resource_id=version.id,
            actor_id=actor_id,
            request_id=request_id,
            before=before,
            after={"status": version.status, "checksum": version.content_checksum},
        )
        return _prompt_payload(session, version)


def update_settings_version(
    engine: Engine,
    version_id: UUID,
    retrieval: RetrievalTuning,
    *,
    actor_id: UUID,
    request_id: UUID,
) -> dict[str, Any]:
    with Session(engine) as session, session.begin():
        version = _settings(session, version_id, lock=True)
        if version.status not in {"DRAFT", "EVALUATED"}:
            raise TuningError(409, "SETTINGS_IMMUTABLE", "Only draft settings can be edited")
        before = {"status": version.status, "checksum": version.content_checksum}
        version.settings_json = retrieval.model_dump(mode="json")
        version.content_checksum = settings_checksum(retrieval)
        version.requires_reindex = False
        version.status = "DRAFT"
        version.updated_at = _now()
        _audit(
            session,
            event_type="settings_version.updated",
            resource_type="settings_version",
            resource_id=version.id,
            actor_id=actor_id,
            request_id=request_id,
            before=before,
            after={"status": version.status, "checksum": version.content_checksum},
        )
        return _settings_payload(session, version)


@contextmanager
def evaluation_lock(engine: Engine) -> Iterator[None]:
    with engine.connect() as connection:
        acquired = connection.scalar(
            text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
            {"key": TUNING_LOCK_KEY},
        )
        if not acquired:
            raise TuningError(409, "EVALUATION_RUNNING", "An evaluation is already running")
        try:
            yield
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                {"key": TUNING_LOCK_KEY},
            )


def list_evaluations(
    engine: Engine,
    *,
    status: Literal["RUNNING", "PASSED", "FAILED"] | None,
    mode: Literal["RETRIEVAL", "FULL"] | None,
    offset: int,
    limit: int,
) -> tuple[tuple[dict[str, Any], ...], int]:
    with Session(engine) as session:
        filters = []
        if status is not None:
            filters.append(EvaluationRun.status == status)
        if mode is not None:
            filters.append(EvaluationRun.mode == mode)
        total = session.scalar(select(func.count()).select_from(EvaluationRun).where(*filters)) or 0
        runs = session.scalars(
            select(EvaluationRun)
            .where(*filters)
            .order_by(EvaluationRun.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return tuple(_evaluation_payload(run) or {} for run in runs), int(total)


def get_evaluation(engine: Engine, run_id: UUID) -> dict[str, Any]:
    with Session(engine) as session:
        run = session.get(EvaluationRun, run_id)
        if run is None:
            raise TuningError(404, "EVALUATION_NOT_FOUND", "Evaluation was not found")
        payload = _evaluation_payload(run)
        assert payload is not None
        payload.update(
            {
                "kb_version_id": run.kb_version_id,
                "kb_manifest_checksum": run.kb_manifest_checksum,
                "prompt_version": run.prompt_version,
                "prompt_checksum": run.prompt_checksum,
                "settings_version": run.settings_version,
                "settings_checksum": run.settings_checksum,
                "model_name": run.model_name,
                "embedding_model": run.embedding_model,
                "embedding_version": run.embedding_version,
                "started_by": run.started_by,
            }
        )
        return payload


def _active_kb(session: Session, dataset_id: str) -> KnowledgeBaseVersion:
    version = session.scalar(
        select(KnowledgeBaseVersion).where(
            KnowledgeBaseVersion.dataset_id == dataset_id,
            KnowledgeBaseVersion.status == "ACTIVE",
        )
    )
    if version is None:
        raise TuningError(409, "ACTIVE_KB_REQUIRED", "An active knowledge base is required")
    return version


def _evaluation_data(dataset_version: str) -> Any:
    from app.evaluation import load_evaluation_data

    data = load_evaluation_data()
    return data.model_copy(
        update={
            "catalog": data.catalog.model_copy(update={"dataset_version": dataset_version}),
            "cases": tuple(
                case.model_copy(update={"dataset_version": dataset_version}) for case in data.cases
            ),
        }
    )


def _complete_orphaned_runs(engine: Engine, actor_id: UUID, request_id: UUID) -> None:
    with Session(engine) as session, session.begin():
        runs = session.scalars(
            select(EvaluationRun).where(EvaluationRun.status == "RUNNING").with_for_update()
        )
        for run in runs:
            run.status = "FAILED"
            run.error_code = "EVALUATION_INTERRUPTED"
            run.metrics_json = {"passed": False, "error": {"code": run.error_code}}
            run.completed_at = _now()
            _audit(
                session,
                event_type="evaluation.interrupted",
                resource_type="evaluation_run",
                resource_id=run.id,
                actor_id=actor_id,
                request_id=request_id,
                before={"status": "RUNNING"},
                after={"status": "FAILED", "error_code": run.error_code},
            )


def _matching_evaluation(
    session: Session,
    *,
    kb: KnowledgeBaseVersion,
    prompt_version: str,
    prompt_checksum_value: str,
    settings_version: str,
    settings_checksum_value: str,
    mode: Literal["RETRIEVAL", "FULL"],
    model_name: str,
    embedding_model: str,
    embedding_version: str,
) -> EvaluationRun | None:
    return session.scalar(
        select(EvaluationRun)
        .where(
            EvaluationRun.kb_version_id == kb.id,
            EvaluationRun.kb_manifest_checksum == kb.manifest_checksum,
            EvaluationRun.prompt_version == prompt_version,
            EvaluationRun.prompt_checksum == prompt_checksum_value,
            EvaluationRun.settings_version == settings_version,
            EvaluationRun.settings_checksum == settings_checksum_value,
            EvaluationRun.mode == mode,
            EvaluationRun.model_name == model_name,
            EvaluationRun.embedding_model == embedding_model,
            EvaluationRun.embedding_version == embedding_version,
            EvaluationRun.status.in_(("PASSED", "FAILED")),
        )
        .order_by(EvaluationRun.completed_at.desc())
        .limit(1)
    )


def _run_evaluation(
    engine: Engine,
    embedding_provider: Any,
    llm_provider: Any | None,
    base: Settings,
    *,
    kb: KnowledgeBaseVersion,
    prompt: PromptVersion,
    settings_version: SettingsVersion,
    mode: Literal["RETRIEVAL", "FULL"],
    suite: str,
    baseline_run_id: UUID | None,
    actor_id: UUID,
    request_id: UUID,
) -> EvaluationRun:
    from app.evaluation import run_evaluation

    prompts = _prompt_bundle(prompt)
    retrieval = _retrieval_tuning(settings_version)
    effective = base.model_copy(
        update={
            **retrieval.model_dump(),
            "prompt_version": prompt.version,
            "settings_version": settings_version.version,
        }
    )
    run_id = uuid4()
    with Session(engine) as session, session.begin():
        session.add(
            EvaluationRun(
                id=run_id,
                suite=suite,
                kb_version_id=kb.id,
                kb_manifest_checksum=kb.manifest_checksum,
                prompt_version=prompt.version,
                settings_version=settings_version.version,
                prompt_checksum=prompt.content_checksum,
                settings_checksum=settings_version.content_checksum,
                mode=mode,
                baseline_run_id=baseline_run_id,
                model_name=base.chat_model if mode == "FULL" else embedding_provider.model_id,
                embedding_model=embedding_provider.model_id,
                embedding_version=embedding_provider.model_version,
                error_code=None,
                status="RUNNING",
                metrics_json={},
                started_by=actor_id,
            )
        )

    failure: Exception | None = None
    try:
        report = run_evaluation(
            engine,
            embedding_provider,
            effective,
            prompts,
            _evaluation_data(kb.dataset_version),
            llm_provider=llm_provider,
            kb_version_id=kb.id,
        )
    except Exception as exc:
        failure = exc
        report = {"passed": False, "error": {"code": "EVALUATION_EXECUTION_FAILED"}}

    with Session(engine) as session, session.begin():
        run = session.get(EvaluationRun, run_id, with_for_update=True)
        current_kb = session.get(KnowledgeBaseVersion, kb.id)
        current_prompt = session.get(PromptVersion, prompt.id)
        current_settings = session.get(SettingsVersion, settings_version.id)
        if run is None or current_kb is None or current_prompt is None or current_settings is None:
            raise TuningError(500, "EVALUATION_STATE_MISSING", "Evaluation state is missing")
        stale = (
            current_kb.manifest_checksum != kb.manifest_checksum
            or current_prompt.content_checksum != prompt.content_checksum
            or current_settings.content_checksum != settings_version.content_checksum
        )
        if stale:
            report = {"passed": False, "error": {"code": "CONFIG_CHANGED_DURING_EVALUATION"}}
        run.status = "PASSED" if report.get("passed") is True else "FAILED"
        run.metrics_json = report
        run.error_code = (
            None
            if run.status == "PASSED"
            else str(report.get("error", {}).get("code", "EVALUATION_GATE_FAILED"))
        )
        run.completed_at = _now()
        _audit(
            session,
            event_type="evaluation.completed",
            resource_type="evaluation_run",
            resource_id=run.id,
            actor_id=actor_id,
            request_id=request_id,
            before={"status": "RUNNING"},
            after={"status": run.status, "error_code": run.error_code},
            metadata={"suite": suite, "mode": mode},
        )
    if failure is not None:
        raise TuningError(
            409, "EVALUATION_FAILED", "The evaluation could not complete"
        ) from failure
    with Session(engine) as session:
        completed = session.get(EvaluationRun, run_id)
        assert completed is not None
        session.expunge(completed)
        return completed


def _comparison(candidate: EvaluationRun, baseline: EvaluationRun) -> dict[str, Any]:
    def summary(run: EvaluationRun) -> dict[str, Any]:
        retrieval = run.metrics_json.get("retrieval", {})
        answering = run.metrics_json.get("answering", {})
        return {
            "run_id": str(run.id),
            "status": run.status,
            "retrieval_passed": retrieval.get("passed_cases"),
            "retrieval_cases": retrieval.get("evaluated_cases"),
            "source_recall_at_k": retrieval.get("source_recall_at_k"),
            "fact_recall_at_k": retrieval.get("fact_recall_at_k"),
            "answering_passed": answering.get("passed_cases"),
            "answering_cases": answering.get("evaluated_cases"),
        }

    return {"baseline": summary(baseline), "candidate": summary(candidate)}


def _evaluate_candidate(
    engine: Engine,
    embedding_provider: Any,
    llm_provider: Any | None,
    base: Settings,
    *,
    candidate_prompt: PromptVersion,
    candidate_settings: SettingsVersion,
    candidate_kind: Literal["PROMPT", "SETTINGS"],
    actor_id: UUID,
    request_id: UUID,
) -> None:
    mode: Literal["RETRIEVAL", "FULL"] = "FULL" if candidate_kind == "PROMPT" else "RETRIEVAL"
    with Session(engine) as session:
        kb = _active_kb(session, base.expected_dataset_id)
        active_prompt = session.scalar(
            select(PromptVersion).where(PromptVersion.status == "ACTIVE")
        )
        active_settings = session.scalar(
            select(SettingsVersion).where(SettingsVersion.status == "ACTIVE")
        )
        assert active_prompt is not None and active_settings is not None
        baseline = _matching_evaluation(
            session,
            kb=kb,
            prompt_version=active_prompt.version,
            prompt_checksum_value=active_prompt.content_checksum,
            settings_version=active_settings.version,
            settings_checksum_value=active_settings.content_checksum,
            mode=mode,
            model_name=base.chat_model if mode == "FULL" else embedding_provider.model_id,
            embedding_model=embedding_provider.model_id,
            embedding_version=embedding_provider.model_version,
        )
        session.expunge(kb)
        session.expunge(active_prompt)
        session.expunge(active_settings)
        if baseline is not None:
            session.expunge(baseline)
    if baseline is None:
        baseline = _run_evaluation(
            engine,
            embedding_provider,
            llm_provider if mode == "FULL" else None,
            base,
            kb=kb,
            prompt=active_prompt,
            settings_version=active_settings,
            mode=mode,
            suite=f"{candidate_kind}_ACTIVE_BASELINE",
            baseline_run_id=None,
            actor_id=actor_id,
            request_id=request_id,
        )
    candidate = _run_evaluation(
        engine,
        embedding_provider,
        llm_provider if mode == "FULL" else None,
        base,
        kb=kb,
        prompt=candidate_prompt,
        settings_version=candidate_settings,
        mode=mode,
        suite=f"{candidate_kind}_CANDIDATE",
        baseline_run_id=baseline.id,
        actor_id=actor_id,
        request_id=request_id,
    )
    with Session(engine) as session, session.begin():
        run = session.get(EvaluationRun, candidate.id, with_for_update=True)
        if run is None:
            raise TuningError(500, "EVALUATION_STATE_MISSING", "Evaluation state is missing")
        run.metrics_json = {**run.metrics_json, "comparison": _comparison(candidate, baseline)}
        if candidate_kind == "PROMPT":
            target: PromptVersion | SettingsVersion = _prompt(
                session, candidate_prompt.id, lock=True
            )
        else:
            target = _settings(session, candidate_settings.id, lock=True)
        if run.status == "PASSED" and target.status != "RETIRED":
            target.status = "EVALUATED"
            target.updated_at = _now()


def evaluate_prompt_version(
    engine: Engine,
    embedding_provider: Any,
    llm_provider: Any,
    base: Settings,
    version_id: UUID,
    *,
    actor_id: UUID,
    request_id: UUID,
) -> dict[str, Any]:
    with evaluation_lock(engine):
        _complete_orphaned_runs(engine, actor_id, request_id)
        with Session(engine) as session:
            candidate = _prompt(session, version_id)
            active_settings = session.scalar(
                select(SettingsVersion).where(SettingsVersion.status == "ACTIVE")
            )
            if candidate.status not in {"DRAFT", "EVALUATED", "RETIRED"}:
                raise TuningError(409, "PROMPT_STATE_INVALID", "Prompt cannot be evaluated")
            if active_settings is None:
                raise TuningError(409, "ACTIVE_SETTINGS_REQUIRED", "Active settings are required")
            session.expunge(candidate)
            session.expunge(active_settings)
        _evaluate_candidate(
            engine,
            embedding_provider,
            llm_provider,
            base,
            candidate_prompt=candidate,
            candidate_settings=active_settings,
            candidate_kind="PROMPT",
            actor_id=actor_id,
            request_id=request_id,
        )
    return get_prompt_version(engine, version_id)


def evaluate_settings_version(
    engine: Engine,
    embedding_provider: Any,
    base: Settings,
    version_id: UUID,
    *,
    actor_id: UUID,
    request_id: UUID,
) -> dict[str, Any]:
    with evaluation_lock(engine):
        _complete_orphaned_runs(engine, actor_id, request_id)
        with Session(engine) as session:
            candidate = _settings(session, version_id)
            active_prompt = session.scalar(
                select(PromptVersion).where(PromptVersion.status == "ACTIVE")
            )
            if candidate.status not in {"DRAFT", "EVALUATED", "RETIRED"}:
                raise TuningError(409, "SETTINGS_STATE_INVALID", "Settings cannot be evaluated")
            if candidate.requires_reindex:
                raise TuningError(
                    409, "SETTINGS_REINDEX_REQUIRED", "Settings requiring reindex cannot activate"
                )
            if active_prompt is None:
                raise TuningError(409, "ACTIVE_PROMPT_REQUIRED", "An active prompt is required")
            session.expunge(candidate)
            session.expunge(active_prompt)
        _evaluate_candidate(
            engine,
            embedding_provider,
            None,
            base,
            candidate_prompt=active_prompt,
            candidate_settings=candidate,
            candidate_kind="SETTINGS",
            actor_id=actor_id,
            request_id=request_id,
        )
    return get_settings_version(engine, version_id)


def _activation_evaluation(
    session: Session,
    base: Settings,
    kb: KnowledgeBaseVersion,
    prompt: PromptVersion,
    settings_version: SettingsVersion,
    *,
    kind: Literal["PROMPT", "SETTINGS"],
) -> EvaluationRun | None:
    mode = "FULL" if kind == "PROMPT" else "RETRIEVAL"
    return session.scalar(
        select(EvaluationRun)
        .where(
            EvaluationRun.kb_version_id == kb.id,
            EvaluationRun.kb_manifest_checksum == kb.manifest_checksum,
            EvaluationRun.prompt_version == prompt.version,
            EvaluationRun.prompt_checksum == prompt.content_checksum,
            EvaluationRun.settings_version == settings_version.version,
            EvaluationRun.settings_checksum == settings_version.content_checksum,
            EvaluationRun.mode == mode,
            EvaluationRun.status == "PASSED",
            EvaluationRun.model_name
            == (base.chat_model if kind == "PROMPT" else base.embedding_model_id),
            EvaluationRun.embedding_model == base.embedding_model_id,
            EvaluationRun.embedding_version == base.embedding_model_revision,
            EvaluationRun.baseline_run_id.is_not(None),
        )
        .order_by(EvaluationRun.completed_at.desc())
        .limit(1)
    )


def activate_runtime_version(
    engine: Engine,
    base: Settings,
    version_id: UUID,
    *,
    kind: Literal["PROMPT", "SETTINGS"],
    rollback: bool,
    actor_id: UUID,
    request_id: UUID,
) -> dict[str, Any]:
    with Session(engine) as session, session.begin():
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": "topmed:runtime-activation"},
        )
        kb = _active_kb(session, base.expected_dataset_id)
        active_prompt = session.scalar(
            select(PromptVersion).where(PromptVersion.status == "ACTIVE").with_for_update()
        )
        active_settings = session.scalar(
            select(SettingsVersion).where(SettingsVersion.status == "ACTIVE").with_for_update()
        )
        if active_prompt is None or active_settings is None:
            raise TuningError(
                409, "ACTIVE_RUNTIME_REQUIRED", "Active runtime versions are required"
            )
        if kind == "PROMPT":
            target: PromptVersion | SettingsVersion = _prompt(session, version_id, lock=True)
            prompt = cast(PromptVersion, target)
            settings_version = active_settings
        else:
            target = _settings(session, version_id, lock=True)
            prompt = active_prompt
            settings_version = target
            if target.requires_reindex:
                raise TuningError(
                    409, "SETTINGS_REINDEX_REQUIRED", "Settings requiring reindex cannot activate"
                )
        expected_status = "RETIRED" if rollback else "EVALUATED"
        if target.status != expected_status:
            raise TuningError(
                409,
                "RUNTIME_VERSION_STATE_INVALID",
                f"Only a {expected_status.lower()} version can be activated",
            )
        evaluation = _activation_evaluation(session, base, kb, prompt, settings_version, kind=kind)
        if evaluation is None:
            raise TuningError(
                409,
                "CURRENT_EVALUATION_REQUIRED",
                "A passing evaluation for the exact active runtime tuple is required",
            )
        previous = active_prompt if kind == "PROMPT" else active_settings
        previous.status = "RETIRED"
        session.flush([previous])
        previous_status = target.status
        target.status = "ACTIVE"
        target.activated_by = actor_id
        target.activated_at = _now()
        target.updated_at = _now()
        resource_type = "prompt_version" if kind == "PROMPT" else "settings_version"
        _audit(
            session,
            event_type=f"{resource_type}.retired",
            resource_type=resource_type,
            resource_id=previous.id,
            actor_id=actor_id,
            request_id=request_id,
            before={"status": "ACTIVE"},
            after={"status": "RETIRED"},
            metadata={"replacement_id": str(target.id)},
        )
        _audit(
            session,
            event_type=f"{resource_type}.{'rolled_back' if rollback else 'activated'}",
            resource_type=resource_type,
            resource_id=target.id,
            actor_id=actor_id,
            request_id=request_id,
            before={"status": previous_status},
            after={"status": "ACTIVE"},
            metadata={
                "previous_active_id": str(previous.id),
                "evaluation_run_id": str(evaluation.id),
            },
        )
    return (
        get_prompt_version(engine, version_id)
        if kind == "PROMPT"
        else get_settings_version(engine, version_id)
    )
