from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.embeddings import EmbeddingError, EmbeddingProvider, embed_knowledge_base
from app.evaluation import EvaluationData, load_evaluation_data, run_evaluation
from app.kb import (
    EXPECTED_DOCUMENT_COUNT,
    KnowledgeImportError,
    activate_knowledge_base,
    chunk_document,
    lock_dataset,
    normalize_content,
    parse_draft_document,
    sha256_bytes,
)
from app.models import (
    AuditEvent,
    Chunk,
    Document,
    DocumentRevision,
    EvaluationRun,
    KnowledgeBaseVersion,
)
from app.tuning import runtime_snapshot

POLICY_ID_PATTERN = re.compile(r"\bTM-[A-Z0-9]+(?:-[A-Z0-9]+)+\b")


class KnowledgeWorkflowError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC)


def _version(
    session: Session,
    version_id: UUID,
    dataset_id: str,
    *,
    lock: bool = False,
) -> KnowledgeBaseVersion:
    query = select(KnowledgeBaseVersion).where(
        KnowledgeBaseVersion.id == version_id,
        KnowledgeBaseVersion.dataset_id == dataset_id,
    )
    version = session.scalar(query.with_for_update() if lock else query)
    if version is None:
        raise KnowledgeWorkflowError(404, "KB_VERSION_NOT_FOUND", "Knowledge version not found")
    return version


def _latest_revision(session: Session, document_id: UUID) -> DocumentRevision:
    revision = session.scalar(
        select(DocumentRevision)
        .where(DocumentRevision.document_id == document_id)
        .order_by(DocumentRevision.revision_number.desc())
        .limit(1)
    )
    if revision is None:
        raise KnowledgeWorkflowError(409, "KB_REVISION_MISSING", "Document revision is missing")
    return revision


def _manifest_checksum(session: Session, version: KnowledgeBaseVersion) -> str:
    documents = [
        (document_key, checksum)
        for document_key, checksum in session.execute(
            select(Document.document_key, Document.checksum)
            .where(Document.kb_version_id == version.id)
            .order_by(Document.document_key)
        )
    ]
    payload = {
        "dataset_id": version.dataset_id,
        "dataset_version": version.dataset_version,
        "documents": documents,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _audit(
    session: Session,
    *,
    event_type: str,
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
            resource_type="knowledge_base_version",
            resource_id=str(resource_id),
            request_id=request_id,
            before_json=before,
            after_json=after,
            metadata_json=metadata or {},
        )
    )


def _latest_evaluation(session: Session, version_id: UUID) -> EvaluationRun | None:
    return session.scalar(
        select(EvaluationRun)
        .where(EvaluationRun.kb_version_id == version_id)
        .order_by(EvaluationRun.started_at.desc())
        .limit(1)
    )


def _version_payload(session: Session, version: KnowledgeBaseVersion) -> dict[str, Any]:
    document_count = session.scalar(
        select(func.count()).select_from(Document).where(Document.kb_version_id == version.id)
    )
    chunk_count = session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.kb_version_id == version.id)
    )
    embedded_count = session.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(
            Chunk.kb_version_id == version.id,
            Chunk.embedding.is_not(None),
            Chunk.embedding_content_checksum == Chunk.content_checksum,
        )
    )
    evaluation = _latest_evaluation(session, version.id)
    return {
        "id": version.id,
        "dataset_id": version.dataset_id,
        "dataset_version": version.dataset_version,
        "status": version.status,
        "source_version_id": version.source_version_id,
        "manifest_checksum": version.manifest_checksum,
        "document_count": int(document_count or 0),
        "chunk_count": int(chunk_count or 0),
        "embedded_chunk_count": int(embedded_count or 0),
        "validation": version.validation_report_json,
        "validated_at": version.validated_at,
        "evaluation": (
            {
                "id": evaluation.id,
                "status": evaluation.status,
                "suite": evaluation.suite,
                "manifest_checksum": evaluation.kb_manifest_checksum,
                "metrics": evaluation.metrics_json,
                "started_at": evaluation.started_at,
                "completed_at": evaluation.completed_at,
            }
            if evaluation
            else None
        ),
        "created_by": version.created_by,
        "activated_by": version.activated_by,
        "created_at": version.created_at,
        "activated_at": version.activated_at,
    }


def list_versions(engine: Engine, *, dataset_id: str) -> tuple[dict[str, Any], ...]:
    with Session(engine) as session:
        versions = session.scalars(
            select(KnowledgeBaseVersion)
            .where(KnowledgeBaseVersion.dataset_id == dataset_id)
            .order_by(KnowledgeBaseVersion.created_at.desc())
        ).all()
        return tuple(_version_payload(session, version) for version in versions)


def get_version(engine: Engine, *, dataset_id: str, version_id: UUID) -> dict[str, Any]:
    with Session(engine) as session:
        version = _version(session, version_id, dataset_id)
        documents = session.execute(
            select(Document, func.count(Chunk.id).label("chunk_count"))
            .outerjoin(Chunk, Chunk.document_id == Document.id)
            .where(Document.kb_version_id == version.id)
            .group_by(Document.id)
            .order_by(Document.sort_order)
        ).all()
        return {
            **_version_payload(session, version),
            "documents": [
                {
                    "id": document.id,
                    "document_key": document.document_key,
                    "title": document.title,
                    "source_path": document.source_path,
                    "checksum": document.checksum,
                    "chunk_count": chunk_count,
                    "sort_order": document.sort_order,
                }
                for document, chunk_count in documents
            ],
        }


def _rewrite_dataset_version(content: str, dataset_version: str) -> str:
    lines = content.splitlines()
    try:
        closing = lines[1:].index("---") + 1
    except ValueError as exc:
        raise KnowledgeWorkflowError(
            409, "KB_FRONT_MATTER_INVALID", "Document front matter is invalid"
        ) from exc
    matches = [
        index
        for index, line in enumerate(lines[1:closing], start=1)
        if line.startswith("dataset_version:")
    ]
    if len(matches) != 1:
        raise KnowledgeWorkflowError(
            409, "KB_FRONT_MATTER_INVALID", "Document must declare one dataset version"
        )
    lines[matches[0]] = f'dataset_version: "{dataset_version}"'
    return "\n".join(lines) + ("\n" if content.endswith("\n") else "")


def _add_chunks(
    session: Session,
    version: KnowledgeBaseVersion,
    document: Document,
    revision: DocumentRevision,
    content: str,
) -> int:
    try:
        parsed = parse_draft_document(
            content,
            document_key=document.document_key,
            dataset_id=version.dataset_id,
            dataset_version=version.dataset_version,
            language=version.language,
            source_path=document.source_path,
        )
        chunks = chunk_document(parsed)
    except KnowledgeImportError as exc:
        raise KnowledgeWorkflowError(422, "KB_DOCUMENT_INVALID", str(exc)) from exc
    document.title = parsed.title
    document.checksum = parsed.manifest_document.sha256
    document.updated_at = _now()
    revision.front_matter = parsed.front_matter
    document.metadata_json = {
        **document.metadata_json,
        "word_count": parsed.manifest_document.word_count,
        "section_count": parsed.manifest_document.section_count,
    }
    for chunk in chunks:
        session.add(
            Chunk(
                id=uuid4(),
                kb_version_id=version.id,
                document_id=document.id,
                revision_id=revision.id,
                stable_chunk_key=chunk.stable_chunk_key,
                section=chunk.section,
                section_path=list(chunk.section_path),
                ordinal=chunk.ordinal,
                content=chunk.content,
                content_normalized=chunk.content_normalized,
                token_count=chunk.token_count,
                metadata_json={
                    **chunk.metadata,
                    "dataset_id": version.dataset_id,
                    "dataset_version": version.dataset_version,
                    "language": version.language,
                    "source_path": document.source_path,
                },
            )
        )
    return len(chunks)


def create_draft(
    engine: Engine,
    *,
    dataset_id: str,
    dataset_version: str,
    actor_id: UUID,
    request_id: UUID,
) -> dict[str, Any]:
    with Session(engine) as session, session.begin():
        lock_dataset(session, dataset_id)
        source = session.scalar(
            select(KnowledgeBaseVersion)
            .where(
                KnowledgeBaseVersion.dataset_id == dataset_id,
                KnowledgeBaseVersion.status == "ACTIVE",
            )
            .with_for_update()
        )
        if source is None:
            raise KnowledgeWorkflowError(409, "ACTIVE_KB_MISSING", "Active knowledge is missing")
        if session.scalar(
            select(KnowledgeBaseVersion.id).where(
                KnowledgeBaseVersion.dataset_id == dataset_id,
                KnowledgeBaseVersion.dataset_version == dataset_version,
            )
        ):
            raise KnowledgeWorkflowError(
                409, "KB_VERSION_EXISTS", "The requested knowledge version already exists"
            )
        draft = KnowledgeBaseVersion(
            id=uuid4(),
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            generator_version=source.generator_version,
            language=source.language,
            seed_checksum=source.seed_checksum,
            template_checksum=source.template_checksum,
            manifest_checksum="0" * 64,
            status="DRAFT",
            source_version_id=source.id,
            created_by=actor_id,
        )
        session.add(draft)
        session.flush()
        documents = session.scalars(
            select(Document)
            .where(Document.kb_version_id == source.id)
            .order_by(Document.sort_order)
        ).all()
        for source_document in documents:
            content = _rewrite_dataset_version(
                _latest_revision(session, source_document.id).content_markdown,
                dataset_version,
            )
            document = Document(
                id=uuid4(),
                kb_version_id=draft.id,
                document_key=source_document.document_key,
                title=source_document.title,
                language=source_document.language,
                source_path=source_document.source_path,
                checksum=sha256_bytes(content.encode()),
                status="IMPORTED",
                sort_order=source_document.sort_order,
                metadata_json=dict(source_document.metadata_json),
            )
            revision = DocumentRevision(
                id=uuid4(),
                document_id=document.id,
                revision_number=1,
                content_markdown=content,
                content_checksum=document.checksum,
                front_matter={},
            )
            session.add_all((document, revision))
            session.flush()
            _add_chunks(session, draft, document, revision, content)
        session.flush()
        draft.manifest_checksum = _manifest_checksum(session, draft)
        _audit(
            session,
            event_type="knowledge_base.draft_created",
            resource_id=draft.id,
            actor_id=actor_id,
            request_id=request_id,
            before=None,
            after={"dataset_version": dataset_version, "status": "DRAFT"},
            metadata={"source_version_id": str(source.id)},
        )
        session.flush()
        return _version_payload(session, draft)


def _dependencies(document_key: str, data: EvaluationData) -> dict[str, list[str]]:
    facts = sorted(
        fact_id
        for fact_id, fact in data.catalog.facts.items()
        if fact.canonical_document == document_key or document_key in fact.acceptable_documents
    )
    cases = sorted(
        case.id
        for case in data.cases
        if set(case.required_fact_ids + case.forbidden_fact_ids).intersection(facts)
    )
    return {"fact_ids": facts, "evaluation_case_ids": cases}


def get_document(
    engine: Engine,
    *,
    dataset_id: str,
    version_id: UUID,
    document_id: UUID,
) -> dict[str, Any]:
    with Session(engine) as session:
        version = _version(session, version_id, dataset_id)
        document = session.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.kb_version_id == version.id,
            )
        )
        if document is None:
            raise KnowledgeWorkflowError(404, "KB_DOCUMENT_NOT_FOUND", "Document not found")
        revision = _latest_revision(session, document.id)
        revisions = session.scalars(
            select(DocumentRevision)
            .where(DocumentRevision.document_id == document.id)
            .order_by(DocumentRevision.revision_number.desc())
        ).all()
        chunks = session.scalars(
            select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.ordinal)
        ).all()
        return {
            "id": document.id,
            "version_id": version.id,
            "dataset_version": version.dataset_version,
            "version_status": version.status,
            "document_key": document.document_key,
            "title": document.title,
            "source_path": document.source_path,
            "checksum": document.checksum,
            "revision_number": revision.revision_number,
            "content_markdown": revision.content_markdown,
            "front_matter": revision.front_matter,
            "revisions": [
                {
                    "revision_number": item.revision_number,
                    "content_checksum": item.content_checksum,
                    "created_at": item.created_at,
                }
                for item in revisions
            ],
            "dependencies": _dependencies(document.document_key, load_evaluation_data()),
            "chunks": [
                {
                    "id": chunk.id,
                    "stable_chunk_key": chunk.stable_chunk_key,
                    "section": chunk.section,
                    "section_path": chunk.section_path,
                    "ordinal": chunk.ordinal,
                    "content": chunk.content,
                    "token_count": chunk.token_count,
                }
                for chunk in chunks
            ],
        }


def update_document(
    engine: Engine,
    *,
    dataset_id: str,
    version_id: UUID,
    document_id: UUID,
    content: str,
    actor_id: UUID,
    request_id: UUID,
) -> dict[str, Any]:
    with Session(engine) as session, session.begin():
        version = _version(session, version_id, dataset_id, lock=True)
        if version.status != "DRAFT":
            raise KnowledgeWorkflowError(409, "KB_VERSION_IMMUTABLE", "Only drafts can be edited")
        document = session.scalar(
            select(Document)
            .where(Document.id == document_id, Document.kb_version_id == version.id)
            .with_for_update()
        )
        if document is None:
            raise KnowledgeWorkflowError(404, "KB_DOCUMENT_NOT_FOUND", "Document not found")
        previous = _latest_revision(session, document.id)
        if previous.content_markdown == content:
            return get_document(
                engine,
                dataset_id=dataset_id,
                version_id=version_id,
                document_id=document_id,
            )
        try:
            parsed = parse_draft_document(
                content,
                document_key=document.document_key,
                dataset_id=version.dataset_id,
                dataset_version=version.dataset_version,
                language=version.language,
                source_path=document.source_path,
            )
        except KnowledgeImportError as exc:
            raise KnowledgeWorkflowError(422, "KB_DOCUMENT_INVALID", str(exc)) from exc
        revision = DocumentRevision(
            id=uuid4(),
            document_id=document.id,
            revision_number=previous.revision_number + 1,
            content_markdown=content,
            content_checksum=sha256_bytes(content.encode()),
            front_matter=parsed.front_matter,
        )
        session.execute(delete(Chunk).where(Chunk.document_id == document.id))
        session.add(revision)
        session.flush()
        chunk_count = _add_chunks(session, version, document, revision, content)
        session.flush()
        old_checksum = version.manifest_checksum
        version.manifest_checksum = _manifest_checksum(session, version)
        version.validated_at = None
        version.validated_checksum = None
        version.validation_report_json = None
        _audit(
            session,
            event_type="knowledge_base.document_revised",
            resource_id=version.id,
            actor_id=actor_id,
            request_id=request_id,
            before={"document_id": str(document.id), "revision": previous.revision_number},
            after={"document_id": str(document.id), "revision": revision.revision_number},
            metadata={
                "document_key": document.document_key,
                "chunk_count": chunk_count,
                "previous_manifest_checksum": old_checksum,
                "manifest_checksum": version.manifest_checksum,
            },
        )
    return get_document(
        engine,
        dataset_id=dataset_id,
        version_id=version_id,
        document_id=document_id,
    )


def _validate(session: Session, version: KnowledgeBaseVersion) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    documents = session.scalars(
        select(Document).where(Document.kb_version_id == version.id).order_by(Document.sort_order)
    ).all()
    contents: dict[str, str] = {}
    policy_documents: dict[str, set[str]] = {}
    total_chunks = 0
    for document in documents:
        revision = _latest_revision(session, document.id)
        contents[document.document_key] = revision.content_markdown
        try:
            parsed = parse_draft_document(
                revision.content_markdown,
                document_key=document.document_key,
                dataset_id=version.dataset_id,
                dataset_version=version.dataset_version,
                language=version.language,
                source_path=document.source_path,
            )
            total_chunks += len(chunk_document(parsed))
        except KnowledgeImportError as exc:
            errors.append(
                {
                    "code": "DOCUMENT_INVALID",
                    "document_key": document.document_key,
                    "message": str(exc),
                }
            )
        for policy_id in set(POLICY_ID_PATTERN.findall(revision.content_markdown)):
            policy_documents.setdefault(policy_id, set()).add(document.document_key)
    if len(documents) != EXPECTED_DOCUMENT_COUNT:
        errors.append(
            {
                "code": "DOCUMENT_COUNT_INVALID",
                "document_key": "",
                "message": f"Expected {EXPECTED_DOCUMENT_COUNT} documents, found {len(documents)}",
            }
        )
    if not 30 <= total_chunks <= 60:
        errors.append(
            {
                "code": "CHUNK_COUNT_INVALID",
                "document_key": "",
                "message": f"Expected 30-60 chunks, generated {total_chunks}",
            }
        )
    duplicates = {
        policy_id: sorted(document_keys)
        for policy_id, document_keys in policy_documents.items()
        if len(document_keys) > 1
    }
    for policy_id, document_keys in duplicates.items():
        errors.append(
            {
                "code": "DUPLICATE_POLICY_ID",
                "document_key": ",".join(document_keys),
                "message": f"Policy {policy_id} appears in multiple documents",
            }
        )
    data = load_evaluation_data()
    for fact_id, fact in data.catalog.facts.items():
        content = contents.get(fact.canonical_document, "")
        if normalize_content(fact.expected_fragment) not in normalize_content(content):
            errors.append(
                {
                    "code": "FACT_FRAGMENT_MISSING",
                    "document_key": fact.canonical_document,
                    "message": (
                        f"Evaluation fact {fact_id} no longer matches its canonical document"
                    ),
                }
            )
    conflicts: list[str] = []
    normalized_documents = {key: normalize_content(value) for key, value in contents.items()}
    for fixture in data.fixtures.values():
        lines = [
            normalize_content(line)
            for line in fixture.document.content.splitlines()
            if len(line.strip()) >= 30 and not line.lstrip().startswith("#")
        ]
        matching_documents = sorted(
            document_key
            for document_key, content in normalized_documents.items()
            if any(line and line in content for line in lines)
        )
        if matching_documents:
            conflicts.append(fixture.id)
            errors.append(
                {
                    "code": "CONFLICT_FIXTURE_PRESENT",
                    "document_key": ",".join(matching_documents),
                    "message": f"Archived conflict fixture {fixture.id} appears in the draft",
                }
            )
    return {
        "passed": not errors,
        "document_count": len(documents),
        "chunk_count": total_chunks,
        "errors": errors,
        "duplicate_policy_ids": duplicates,
        "conflict_fixture_ids": sorted(conflicts),
    }


def validate_version(
    engine: Engine,
    *,
    dataset_id: str,
    version_id: UUID,
    actor_id: UUID,
    request_id: UUID,
) -> dict[str, Any]:
    with Session(engine) as session, session.begin():
        version = _version(session, version_id, dataset_id, lock=True)
        if version.status != "DRAFT":
            raise KnowledgeWorkflowError(
                409, "KB_VERSION_IMMUTABLE", "Only drafts can be validated"
            )
        report = _validate(session, version)
        version.validated_at = _now()
        version.validated_checksum = version.manifest_checksum
        version.validation_report_json = report
        _audit(
            session,
            event_type="knowledge_base.validated",
            resource_id=version.id,
            actor_id=actor_id,
            request_id=request_id,
            before=None,
            after={"passed": report["passed"]},
            metadata={"error_count": len(report["errors"])},
        )
        return report


def index_version(
    engine: Engine,
    provider: EmbeddingProvider,
    settings: Settings,
    *,
    dataset_id: str,
    version_id: UUID,
    actor_id: UUID,
    request_id: UUID,
) -> dict[str, Any]:
    with Session(engine) as session:
        version = _version(session, version_id, dataset_id)
        validation = version.validation_report_json or {}
        if (
            validation.get("passed") is not True
            or version.validated_checksum != version.manifest_checksum
        ):
            raise KnowledgeWorkflowError(
                409, "KB_VALIDATION_REQUIRED", "Current successful validation is required"
            )
        version_checksum = version.manifest_checksum
        dataset_version = version.dataset_version
    try:
        embed_knowledge_base(
            engine,
            provider,
            batch_size=settings.embedding_batch_size,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            expected_manifest_checksum=version_checksum,
            actor_type="ADMIN",
            actor_id=str(actor_id),
            request_id=request_id,
        )
    except EmbeddingError as exc:
        raise KnowledgeWorkflowError(409, "KB_INDEXING_FAILED", str(exc)) from exc
    return get_version(engine, dataset_id=dataset_id, version_id=version_id)


def _retarget(data: EvaluationData, dataset_version: str) -> EvaluationData:
    return data.model_copy(
        update={
            "catalog": data.catalog.model_copy(update={"dataset_version": dataset_version}),
            "cases": tuple(
                case.model_copy(update={"dataset_version": dataset_version}) for case in data.cases
            ),
        }
    )


def evaluate_version(
    engine: Engine,
    provider: EmbeddingProvider,
    settings: Settings,
    *,
    dataset_id: str,
    version_id: UUID,
    actor_id: UUID,
    request_id: UUID,
) -> dict[str, Any]:
    run_id = uuid4()
    try:
        with Session(engine) as session, session.begin():
            version = _version(session, version_id, dataset_id, lock=True)
            validation = version.validation_report_json or {}
            if (
                version.status != "DRAFT"
                or validation.get("passed") is not True
                or version.validated_checksum != version.manifest_checksum
            ):
                raise KnowledgeWorkflowError(
                    409, "KB_VALIDATION_REQUIRED", "Current successful validation is required"
                )
            chunks = session.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.kb_version_id == version.id)
            )
            embedded = session.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(
                    Chunk.kb_version_id == version.id,
                    Chunk.embedding.is_not(None),
                    Chunk.embedding_model == provider.model_id,
                    Chunk.embedding_version == provider.model_version,
                    Chunk.embedding_dimensions == provider.dimensions,
                    Chunk.embedding_content_checksum == Chunk.content_checksum,
                )
            )
            if not chunks or embedded != chunks:
                raise KnowledgeWorkflowError(
                    409, "KB_INDEXING_REQUIRED", "Complete current indexing is required"
                )
            checksum = version.manifest_checksum
            dataset_version = version.dataset_version
            runtime = runtime_snapshot(session, settings)
            session.add(
                EvaluationRun(
                    id=run_id,
                    suite="KNOWLEDGE_PUBLISH_RETRIEVAL",
                    kb_version_id=version.id,
                    kb_manifest_checksum=checksum,
                    prompt_version=runtime.prompt_version,
                    settings_version=runtime.settings_version,
                    prompt_checksum=runtime.prompt_checksum,
                    settings_checksum=runtime.settings_checksum,
                    mode="RETRIEVAL",
                    baseline_run_id=None,
                    model_name=provider.model_id,
                    embedding_model=provider.model_id,
                    embedding_version=provider.model_version,
                    error_code=None,
                    status="RUNNING",
                    metrics_json={},
                    started_by=actor_id,
                )
            )
    except IntegrityError as exc:
        raise KnowledgeWorkflowError(
            409, "KB_EVALUATION_RUNNING", "An evaluation is already running"
        ) from exc

    failure: Exception | None = None
    try:
        report = run_evaluation(
            engine,
            provider,
            runtime.effective_settings,
            runtime.prompts,
            _retarget(load_evaluation_data(), dataset_version),
            kb_version_id=version_id,
        )
    except Exception as exc:
        # Persist a terminal run even when a provider or database read fails.
        failure = exc
        report = {"passed": False, "error": {"code": "EVALUATION_EXECUTION_FAILED"}}

    with Session(engine) as session, session.begin():
        version = _version(session, version_id, dataset_id, lock=True)
        run = session.get(EvaluationRun, run_id, with_for_update=True)
        if run is None:
            raise KnowledgeWorkflowError(500, "KB_EVALUATION_MISSING", "Evaluation run is missing")
        if version.manifest_checksum != checksum:
            report = {"passed": False, "error": {"code": "DRAFT_CHANGED_DURING_EVALUATION"}}
        run.status = "PASSED" if report.get("passed") is True else "FAILED"
        run.metrics_json = report
        run.completed_at = _now()
        _audit(
            session,
            event_type="knowledge_base.evaluated",
            resource_id=version.id,
            actor_id=actor_id,
            request_id=request_id,
            before=None,
            after={"evaluation_run_id": str(run.id), "status": run.status},
            metadata={"suite": run.suite, "manifest_checksum": checksum},
        )
    if failure is not None:
        raise KnowledgeWorkflowError(
            409, "KB_EVALUATION_FAILED", "The retrieval evaluation could not complete"
        ) from failure
    return report


def publish_version(
    engine: Engine,
    settings: Settings,
    *,
    dataset_id: str,
    version_id: UUID,
    actor_id: UUID,
    request_id: UUID,
    rollback: bool = False,
) -> dict[str, Any]:
    with Session(engine) as session:
        version = _version(session, version_id, dataset_id)
        runtime = runtime_snapshot(session, settings)
        expected_status = "RETIRED" if rollback else "DRAFT"
        if version.status != expected_status:
            action = "rolled back" if rollback else "activated"
            raise KnowledgeWorkflowError(
                409,
                "KB_VERSION_STATE_INVALID",
                f"Only a {expected_status.lower()} version can be {action}",
            )
        dataset_version = version.dataset_version
    try:
        activate_knowledge_base(
            engine,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            expected_embedding_model=settings.embedding_model_id,
            expected_embedding_version=settings.embedding_model_revision,
            expected_embedding_dimensions=settings.embedding_dimensions,
            expected_prompt_version=runtime.prompt_version,
            expected_prompt_checksum=runtime.prompt_checksum,
            expected_settings_version=runtime.settings_version,
            expected_settings_checksum=runtime.settings_checksum,
            actor_type="ADMIN",
            actor_id=str(actor_id),
            request_id=request_id,
        )
    except KnowledgeImportError as exc:
        raise KnowledgeWorkflowError(409, "KB_ACTIVATION_BLOCKED", str(exc)) from exc
    return get_version(engine, dataset_id=dataset_id, version_id=version_id)
