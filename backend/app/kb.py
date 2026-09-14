from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import yaml
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    Chunk,
    Document,
    DocumentRevision,
    EvaluationRun,
    KnowledgeBaseVersion,
)

MIN_CHUNK_TOKENS = 150
MAX_CHUNK_TOKENS = 350
EXPECTED_DOCUMENT_COUNT = 15
TOKEN_PATTERN = re.compile(r"\w+(?:[-']\w+)*|[^\w\s]", re.UNICODE)
WORD_PATTERN = re.compile(r"\b\w+(?:[-']\w+)*\b", re.UNICODE)
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
HTML_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)


class KnowledgeImportError(RuntimeError):
    pass


def lock_dataset(session: Session, dataset_id: str) -> None:
    session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(dataset_id, 0))))


@dataclass(frozen=True)
class ManifestDocument:
    document_id: str
    path: str
    sha256: str
    word_count: int
    section_count: int


@dataclass(frozen=True)
class Manifest:
    path: Path
    checksum: str
    dataset_id: str
    dataset_version: str
    generator_version: str
    language: str
    seed_checksum: str
    template_checksum: str
    documents: tuple[ManifestDocument, ...]


@dataclass(frozen=True)
class SourceUnit:
    section_path: tuple[str, ...]
    content: str
    source_order: int


@dataclass(frozen=True)
class ParsedDocument:
    manifest_document: ManifestDocument
    title: str
    language: str
    front_matter: dict[str, Any]
    content_markdown: str
    units: tuple[SourceUnit, ...]


@dataclass(frozen=True)
class ChunkSpec:
    stable_chunk_key: str
    section: str
    section_path: tuple[str, ...]
    ordinal: int
    content: str
    content_normalized: str
    token_count: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PreparedDocument:
    parsed: ParsedDocument
    chunks: tuple[ChunkSpec, ...]


@dataclass(frozen=True)
class ImportResult:
    kb_version_id: UUID
    dataset_id: str
    dataset_version: str
    status: str
    document_count: int
    chunk_count: int
    no_op: bool


@dataclass(frozen=True)
class ActivationResult:
    kb_version_id: UUID
    dataset_id: str
    dataset_version: str
    previous_version_id: UUID | None
    no_op: bool


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def token_count(content: str) -> int:
    return len(TOKEN_PATTERN.findall(content))


def word_count(content: str) -> int:
    return len(WORD_PATTERN.findall(content))


def normalize_content(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-")
    return slug or "section"


def _require_string(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise KnowledgeImportError(f"{context} requires a non-empty string field: {key}")
    return value


def _require_positive_int(mapping: dict[str, Any], key: str, context: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise KnowledgeImportError(f"{context} requires a positive integer field: {key}")
    return value


def _require_sha256(mapping: dict[str, Any], key: str, context: str) -> str:
    value = _require_string(mapping, key, context)
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise KnowledgeImportError(f"{context} has an invalid SHA-256 field: {key}")
    return value


def load_manifest(path: Path) -> Manifest:
    manifest_path = path.resolve()
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        raise KnowledgeImportError(f"Could not read manifest: {manifest_path}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise KnowledgeImportError(f"Manifest is not valid JSON: {manifest_path}") from exc
    if not isinstance(data, dict):
        raise KnowledgeImportError("Manifest root must be a JSON object")

    raw_documents = data.get("documents")
    if not isinstance(raw_documents, list) or len(raw_documents) != EXPECTED_DOCUMENT_COUNT:
        raise KnowledgeImportError(
            f"Manifest must contain exactly {EXPECTED_DOCUMENT_COUNT} documents"
        )

    documents: list[ManifestDocument] = []
    document_ids: set[str] = set()
    paths: set[str] = set()
    for index, item in enumerate(raw_documents, start=1):
        context = f"manifest document {index}"
        if not isinstance(item, dict):
            raise KnowledgeImportError(f"{context} must be an object")
        document_id = _require_string(item, "document_id", context)
        relative_path = _require_string(item, "path", context)
        checksum = _require_sha256(item, "sha256", context)
        if document_id in document_ids:
            raise KnowledgeImportError(f"Duplicate document ID in manifest: {document_id}")
        if relative_path in paths:
            raise KnowledgeImportError(f"Duplicate document path in manifest: {relative_path}")
        document_ids.add(document_id)
        paths.add(relative_path)
        candidate = (manifest_path.parent / relative_path).resolve()
        if manifest_path.parent not in candidate.parents or candidate.suffix != ".md":
            raise KnowledgeImportError(f"Unsafe document path in manifest: {relative_path}")
        documents.append(
            ManifestDocument(
                document_id=document_id,
                path=relative_path,
                sha256=checksum,
                word_count=_require_positive_int(item, "word_count", context),
                section_count=_require_positive_int(item, "section_count", context),
            )
        )

    return Manifest(
        path=manifest_path,
        checksum=sha256_bytes(raw),
        dataset_id=_require_string(data, "dataset_id", "manifest"),
        dataset_version=_require_string(data, "dataset_version", "manifest"),
        generator_version=_require_string(data, "generator_version", "manifest"),
        language=_require_string(data, "language", "manifest"),
        seed_checksum=_require_sha256(data, "seed_checksum", "manifest"),
        template_checksum=_require_sha256(data, "template_checksum", "manifest"),
        documents=tuple(documents),
    )


def _split_front_matter(content: str, source_path: Path) -> tuple[dict[str, Any], str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise KnowledgeImportError(f"Document has no YAML front matter: {source_path}")
    try:
        closing_index = next(
            index for index, line in enumerate(lines[1:], start=1) if line == "---"
        )
    except StopIteration as exc:
        raise KnowledgeImportError(
            f"Document has unterminated YAML front matter: {source_path}"
        ) from exc
    try:
        front_matter = yaml.safe_load("\n".join(lines[1:closing_index]))
    except yaml.YAMLError as exc:
        raise KnowledgeImportError(f"Document front matter is invalid YAML: {source_path}") from exc
    if not isinstance(front_matter, dict):
        raise KnowledgeImportError(f"Document front matter must be a mapping: {source_path}")
    body = "\n".join(lines[closing_index + 1 :]).strip()
    if not body:
        raise KnowledgeImportError(f"Document body is empty: {source_path}")
    return front_matter, body


def _parse_units(body: str, source_path: Path) -> tuple[SourceUnit, ...]:
    cleaned = HTML_COMMENT_PATTERN.sub("", body).strip()
    heading_stack: list[str] = []
    current_path: tuple[str, ...] = ()
    current_lines: list[str] = []
    units: list[SourceUnit] = []

    def flush() -> None:
        if not current_lines:
            return
        content = "\n".join(current_lines).strip()
        if content:
            units.append(
                SourceUnit(
                    section_path=current_path,
                    content=content,
                    source_order=len(units) + 1,
                )
            )

    for line in cleaned.splitlines():
        match = HEADING_PATTERN.match(line)
        if match:
            flush()
            current_lines = []
            level = len(match.group(1))
            title = match.group(2).strip()
            heading_stack[level - 1 :] = [title]
            current_path = tuple(heading_stack)
            current_lines.append(f"{'#' * level} {title}")
        else:
            current_lines.append(line.rstrip())
    flush()

    if not units or not units[0].section_path:
        raise KnowledgeImportError(f"Document must begin with a Markdown heading: {source_path}")
    return tuple(units)


def parse_document(manifest: Manifest, document: ManifestDocument) -> ParsedDocument:
    source_path = (manifest.path.parent / document.path).resolve()
    try:
        raw = source_path.read_bytes()
    except OSError as exc:
        raise KnowledgeImportError(f"Could not read document: {source_path}") from exc
    actual_checksum = sha256_bytes(raw)
    if actual_checksum != document.sha256:
        raise KnowledgeImportError(
            f"Checksum mismatch for {document.path}: "
            f"expected {document.sha256}, got {actual_checksum}"
        )
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise KnowledgeImportError(f"Document is not valid UTF-8: {document.path}") from exc
    parsed = parse_draft_document(
        content,
        document_key=document.document_id,
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        language=manifest.language,
        source_path=document.path,
    )
    actual_word_count = word_count(content)
    if actual_word_count != document.word_count:
        raise KnowledgeImportError(
            f"{document.path} declares {document.word_count} words but contains {actual_word_count}"
        )
    if len(parsed.units) != document.section_count:
        raise KnowledgeImportError(
            f"{document.path} declares {document.section_count} sections "
            f"but parsed {len(parsed.units)}"
        )
    return ParsedDocument(
        manifest_document=document,
        title=parsed.title,
        language=parsed.language,
        front_matter=parsed.front_matter,
        content_markdown=parsed.content_markdown,
        units=parsed.units,
    )


def parse_draft_document(
    content: str,
    *,
    document_key: str,
    dataset_id: str,
    dataset_version: str,
    language: str,
    source_path: str,
) -> ParsedDocument:
    try:
        raw = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise KnowledgeImportError(f"Document is not valid UTF-8: {source_path}") from exc
    front_matter, body = _split_front_matter(content, Path(source_path))
    expected_metadata = {
        "document_id": document_key,
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "language": language,
    }
    for key, expected in expected_metadata.items():
        if str(front_matter.get(key)) != expected:
            raise KnowledgeImportError(f"{source_path} front matter {key} must be {expected!r}")
    title = _require_string(front_matter, "title", source_path)
    units = _parse_units(body, Path(source_path))
    return ParsedDocument(
        manifest_document=ManifestDocument(
            document_id=document_key,
            path=source_path,
            sha256=sha256_bytes(raw),
            word_count=word_count(content),
            section_count=len(units),
        ),
        title=title,
        language=language,
        front_matter=front_matter,
        content_markdown=content,
        units=units,
    )


def _split_long_text(text: str, budget: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    parts: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        if token_count(sentence) > budget:
            words = sentence.split()
            while words:
                segment: list[str] = []
                while words and token_count(" ".join([*segment, words[0]])) <= budget:
                    segment.append(words.pop(0))
                if not segment:
                    segment.append(words.pop(0))
                if current:
                    parts.append(" ".join(current))
                    current = []
                parts.append(" ".join(segment))
            continue
        proposed = " ".join([*current, sentence])
        if current and token_count(proposed) > budget:
            parts.append(" ".join(current))
            current = [sentence]
        else:
            current.append(sentence)
    if current:
        parts.append(" ".join(current))
    return parts


def _body_blocks(unit: SourceUnit) -> list[str]:
    lines = unit.content.splitlines()
    if lines and HEADING_PATTERN.match(lines[0]):
        lines = lines[1:]
    blocks = [block.strip() for block in re.split(r"\n\s*\n", "\n".join(lines)) if block.strip()]
    grouped: list[str] = []
    for block in blocks:
        if block.lstrip().startswith("|") and grouped:
            grouped[-1] = f"{grouped[-1]}\n\n{block}"
        else:
            grouped.append(block)
    return grouped


def _split_oversized_unit(unit: SourceUnit, document_title: str) -> list[SourceUnit]:
    prefix_level = min(max(len(unit.section_path), 1), 6)
    prefix = f"{'#' * prefix_level} {unit.section_path[-1]}"
    document_prefix_tokens = token_count(f"# {document_title}\n\n")
    body_budget = MAX_CHUNK_TOKENS - document_prefix_tokens - token_count(prefix) - 2
    blocks: list[str] = []
    for block in _body_blocks(unit):
        if token_count(block) <= body_budget:
            blocks.append(block)
        else:
            blocks.extend(_split_long_text(block, body_budget))

    segments: list[SourceUnit] = []
    current: list[str] = []
    for block in blocks:
        proposed_body = "\n\n".join([*current, block])
        proposed = f"{prefix}\n\n{proposed_body}"
        if current and token_count(f"# {document_title}\n\n{proposed}") > MAX_CHUNK_TOKENS:
            current_body = "\n\n".join(current)
            segments.append(
                SourceUnit(
                    unit.section_path,
                    f"{prefix}\n\n{current_body}",
                    unit.source_order,
                )
            )
            current = [block]
        else:
            current.append(block)
    if current:
        current_body = "\n\n".join(current)
        segments.append(
            SourceUnit(
                unit.section_path,
                f"{prefix}\n\n{current_body}",
                unit.source_order,
            )
        )
    return segments or [unit]


def _bucket_content(title: str, units: list[SourceUnit]) -> str:
    bodies = [unit.content for unit in units]
    if bodies and bodies[0].startswith("# "):
        return "\n\n".join(bodies).strip()
    return f"# {title}\n\n" + "\n\n".join(bodies).strip()


def chunk_document(parsed: ParsedDocument) -> tuple[ChunkSpec, ...]:
    expanded: list[SourceUnit] = []
    for unit in parsed.units:
        if token_count(_bucket_content(parsed.title, [unit])) > MAX_CHUNK_TOKENS:
            expanded.extend(_split_oversized_unit(unit, parsed.title))
        else:
            expanded.append(unit)

    buckets: list[list[SourceUnit]] = []
    current: list[SourceUnit] = []
    for unit in expanded:
        proposed = [*current, unit]
        if current and token_count(_bucket_content(parsed.title, proposed)) > MAX_CHUNK_TOKENS:
            buckets.append(current)
            current = [unit]
        else:
            current = proposed
    if current:
        buckets.append(current)

    if (
        len(buckets) > 1
        and token_count(_bucket_content(parsed.title, buckets[-1])) < MIN_CHUNK_TOKENS
    ):
        previous = buckets[-2]
        last = buckets[-1]
        while len(previous) > 1:
            candidate = previous[-1]
            reduced_previous = previous[:-1]
            expanded_last = [candidate, *last]
            if (
                token_count(_bucket_content(parsed.title, reduced_previous)) >= MIN_CHUNK_TOKENS
                and token_count(_bucket_content(parsed.title, expanded_last)) <= MAX_CHUNK_TOKENS
            ):
                buckets[-2] = reduced_previous
                buckets[-1] = expanded_last
                break
            previous = reduced_previous
        if token_count(_bucket_content(parsed.title, buckets[-1])) < MIN_CHUNK_TOKENS:
            merged = buckets[-2] + buckets[-1]
            if token_count(_bucket_content(parsed.title, merged)) <= MAX_CHUNK_TOKENS:
                buckets[-2:] = [merged]

    chunks: list[ChunkSpec] = []
    for ordinal, bucket in enumerate(buckets, start=1):
        content = _bucket_content(parsed.title, bucket)
        count = token_count(content)
        if count > MAX_CHUNK_TOKENS:
            raise KnowledgeImportError(
                f"Chunking exceeded {MAX_CHUNK_TOKENS} tokens for {parsed.manifest_document.path}"
            )
        section_paths = [list(unit.section_path) for unit in bucket]
        leaf_sections: list[str] = []
        for unit in bucket:
            leaf = unit.section_path[-1]
            if leaf != parsed.title and leaf not in leaf_sections:
                leaf_sections.append(leaf)
        if not leaf_sections:
            leaf_sections = [parsed.title]
        section = " / ".join(leaf_sections)
        stable_section_path = bucket[0].section_path
        key_section = slugify("--".join(stable_section_path))
        chunks.append(
            ChunkSpec(
                stable_chunk_key=(
                    f"{parsed.manifest_document.document_id}__{key_section}__{ordinal:03d}"
                ),
                section=section,
                section_path=stable_section_path,
                ordinal=ordinal,
                content=content,
                content_normalized=normalize_content(content),
                token_count=count,
                metadata={
                    "document_id": parsed.manifest_document.document_id,
                    "document_title": parsed.title,
                    "section_paths": section_paths,
                    "source_orders": [unit.source_order for unit in bucket],
                },
            )
        )
    return tuple(chunks)


def prepare_dataset(manifest_path: Path) -> tuple[Manifest, tuple[PreparedDocument, ...]]:
    manifest = load_manifest(manifest_path)
    prepared: list[PreparedDocument] = []
    for item in manifest.documents:
        parsed = parse_document(manifest, item)
        prepared.append(PreparedDocument(parsed=parsed, chunks=chunk_document(parsed)))
    total_chunks = sum(len(item.chunks) for item in prepared)
    if not 30 <= total_chunks <= 60:
        raise KnowledgeImportError(
            f"Dataset produced {total_chunks} chunks; expected the canonical range 30-60"
        )
    return manifest, tuple(prepared)


def _verify_version_is_indexed(
    session: Session,
    version: KnowledgeBaseVersion,
    *,
    expected_embedding_model: str,
    expected_embedding_version: str,
    expected_embedding_dimensions: int,
) -> None:
    chunk_count = session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.kb_version_id == version.id)
    )
    indexed_count = session.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(
            Chunk.kb_version_id == version.id,
            Chunk.embedding.is_not(None),
            Chunk.embedding_model == expected_embedding_model,
            Chunk.embedding_version == expected_embedding_version,
            Chunk.embedding_dimensions == expected_embedding_dimensions,
            Chunk.embedding_content_checksum == Chunk.content_checksum,
        )
    )
    if not chunk_count or indexed_count != chunk_count:
        raise KnowledgeImportError(
            "Knowledge-base activation requires a complete, current embedding index"
        )


def _activate_version(
    session: Session,
    version: KnowledgeBaseVersion,
    *,
    expected_embedding_model: str,
    expected_embedding_version: str,
    expected_embedding_dimensions: int,
    expected_prompt_version: str | None = None,
    expected_prompt_checksum: str | None = None,
    expected_settings_version: str | None = None,
    expected_settings_checksum: str | None = None,
    actor_type: str = "SYSTEM",
    actor_id: str | None = None,
    request_id: UUID | None = None,
) -> ActivationResult:
    _verify_version_is_indexed(
        session,
        version,
        expected_embedding_model=expected_embedding_model,
        expected_embedding_version=expected_embedding_version,
        expected_embedding_dimensions=expected_embedding_dimensions,
    )
    if version.status == "ACTIVE":
        return ActivationResult(
            kb_version_id=version.id,
            dataset_id=version.dataset_id,
            dataset_version=version.dataset_version,
            previous_version_id=None,
            no_op=True,
        )
    if version.status not in {"DRAFT", "RETIRED"}:
        raise KnowledgeImportError(
            f"Knowledge-base version in state {version.status!r} cannot be activated"
        )
    if version.status == "DRAFT" and version.source_version_id is not None:
        validation = version.validation_report_json or {}
        if (
            validation.get("passed") is not True
            or version.validated_checksum != version.manifest_checksum
        ):
            raise KnowledgeImportError(
                "Knowledge-base activation requires current successful validation"
            )
        evaluation = session.scalar(
            select(EvaluationRun)
            .where(
                EvaluationRun.kb_version_id == version.id,
                EvaluationRun.status == "PASSED",
                EvaluationRun.kb_manifest_checksum == version.manifest_checksum,
                *(
                    (EvaluationRun.prompt_version == expected_prompt_version,)
                    if expected_prompt_version is not None
                    else ()
                ),
                *(
                    (EvaluationRun.prompt_checksum == expected_prompt_checksum,)
                    if expected_prompt_checksum is not None
                    else ()
                ),
                *(
                    (EvaluationRun.settings_version == expected_settings_version,)
                    if expected_settings_version is not None
                    else ()
                ),
                *(
                    (EvaluationRun.settings_checksum == expected_settings_checksum,)
                    if expected_settings_checksum is not None
                    else ()
                ),
            )
            .order_by(EvaluationRun.completed_at.desc())
            .limit(1)
        )
        if evaluation is None:
            raise KnowledgeImportError(
                "Knowledge-base activation requires a passing evaluation for this draft"
            )
    previous = session.scalar(
        select(KnowledgeBaseVersion)
        .where(
            KnowledgeBaseVersion.dataset_id == version.dataset_id,
            KnowledgeBaseVersion.status == "ACTIVE",
        )
        .with_for_update()
    )
    previous_version_id: UUID | None = None
    if previous is not None and previous.id != version.id:
        previous_version_id = previous.id
        previous.status = "RETIRED"
        session.flush([previous])
    previous_status = version.status
    version.status = "ACTIVE"
    version.activated_at = datetime.now(UTC)
    version.activated_by = UUID(actor_id) if actor_id else None
    event_type = (
        "knowledge_base.rolled_back" if previous_status == "RETIRED" else "knowledge_base.activated"
    )
    session.add(
        AuditEvent(
            id=uuid4(),
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            resource_type="knowledge_base_version",
            resource_id=str(version.id),
            request_id=request_id,
            before_json={
                "dataset_id": version.dataset_id,
                "dataset_version": version.dataset_version,
                "status": previous_status,
            },
            after_json={
                "dataset_id": version.dataset_id,
                "dataset_version": version.dataset_version,
                "status": "ACTIVE",
            },
            metadata_json={
                "previous_active_version_id": (
                    str(previous_version_id) if previous_version_id else None
                )
            },
        )
    )
    session.flush()
    return ActivationResult(
        kb_version_id=version.id,
        dataset_id=version.dataset_id,
        dataset_version=version.dataset_version,
        previous_version_id=previous_version_id,
        no_op=False,
    )


def activate_knowledge_base(
    engine: Engine,
    *,
    dataset_id: str,
    dataset_version: str,
    expected_embedding_model: str,
    expected_embedding_version: str,
    expected_embedding_dimensions: int,
    expected_prompt_version: str | None = None,
    expected_prompt_checksum: str | None = None,
    expected_settings_version: str | None = None,
    expected_settings_checksum: str | None = None,
    actor_type: str = "SYSTEM",
    actor_id: str | None = None,
    request_id: UUID | None = None,
) -> ActivationResult:
    with Session(engine) as session, session.begin():
        lock_dataset(session, dataset_id)
        version = session.scalar(
            select(KnowledgeBaseVersion)
            .where(
                KnowledgeBaseVersion.dataset_id == dataset_id,
                KnowledgeBaseVersion.dataset_version == dataset_version,
            )
            .with_for_update()
        )
        if version is None:
            raise KnowledgeImportError(
                f"Knowledge-base version does not exist: {dataset_id}:{dataset_version}"
            )
        return _activate_version(
            session,
            version,
            expected_embedding_model=expected_embedding_model,
            expected_embedding_version=expected_embedding_version,
            expected_embedding_dimensions=expected_embedding_dimensions,
            expected_prompt_version=expected_prompt_version,
            expected_prompt_checksum=expected_prompt_checksum,
            expected_settings_version=expected_settings_version,
            expected_settings_checksum=expected_settings_checksum,
            actor_type=actor_type,
            actor_id=actor_id,
            request_id=request_id,
        )


def import_knowledge_base(
    engine: Engine,
    manifest_path: Path,
) -> ImportResult:
    manifest, prepared_documents = prepare_dataset(manifest_path)
    with Session(engine) as session, session.begin():
        lock_dataset(session, manifest.dataset_id)
        existing = session.scalar(
            select(KnowledgeBaseVersion)
            .where(
                KnowledgeBaseVersion.dataset_id == manifest.dataset_id,
                KnowledgeBaseVersion.dataset_version == manifest.dataset_version,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.manifest_checksum != manifest.checksum:
                raise KnowledgeImportError(
                    "Existing dataset/version has a different manifest checksum; "
                    "active knowledge is immutable"
                )
            document_count = session.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.kb_version_id == existing.id)
            )
            chunk_count = session.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.kb_version_id == existing.id)
            )
            return ImportResult(
                kb_version_id=existing.id,
                dataset_id=existing.dataset_id,
                dataset_version=existing.dataset_version,
                status=existing.status,
                document_count=int(document_count or 0),
                chunk_count=int(chunk_count or 0),
                no_op=True,
            )

        version = KnowledgeBaseVersion(
            id=uuid4(),
            dataset_id=manifest.dataset_id,
            dataset_version=manifest.dataset_version,
            generator_version=manifest.generator_version,
            language=manifest.language,
            seed_checksum=manifest.seed_checksum,
            template_checksum=manifest.template_checksum,
            manifest_checksum=manifest.checksum,
            status="DRAFT",
        )
        session.add(version)
        session.flush()

        chunk_total = 0
        for sort_order, prepared in enumerate(prepared_documents, start=1):
            parsed = prepared.parsed
            document_id = uuid4()
            revision_id = uuid4()
            source_path = f"knowledge_base/{parsed.manifest_document.path}"
            session.add(
                Document(
                    id=document_id,
                    kb_version_id=version.id,
                    document_key=parsed.manifest_document.document_id,
                    title=parsed.title,
                    language=parsed.language,
                    source_path=source_path,
                    checksum=parsed.manifest_document.sha256,
                    status="IMPORTED",
                    sort_order=sort_order,
                    metadata_json={
                        "word_count": parsed.manifest_document.word_count,
                        "section_count": parsed.manifest_document.section_count,
                    },
                )
            )
            session.flush()
            session.add(
                DocumentRevision(
                    id=revision_id,
                    document_id=document_id,
                    revision_number=1,
                    content_markdown=parsed.content_markdown,
                    content_checksum=parsed.manifest_document.sha256,
                    front_matter=parsed.front_matter,
                )
            )
            session.flush()
            for chunk in prepared.chunks:
                session.add(
                    Chunk(
                        id=uuid4(),
                        kb_version_id=version.id,
                        document_id=document_id,
                        revision_id=revision_id,
                        stable_chunk_key=chunk.stable_chunk_key,
                        section=chunk.section,
                        section_path=list(chunk.section_path),
                        ordinal=chunk.ordinal,
                        content=chunk.content,
                        content_normalized=chunk.content_normalized,
                        token_count=chunk.token_count,
                        metadata_json={
                            **chunk.metadata,
                            "dataset_id": manifest.dataset_id,
                            "dataset_version": manifest.dataset_version,
                            "language": manifest.language,
                            "source_path": source_path,
                        },
                    )
                )
                chunk_total += 1

        session.add(
            AuditEvent(
                id=uuid4(),
                event_type="knowledge_base.imported",
                actor_type="SYSTEM",
                resource_type="knowledge_base_version",
                resource_id=str(version.id),
                before_json=None,
                after_json={
                    "dataset_id": manifest.dataset_id,
                    "dataset_version": manifest.dataset_version,
                    "status": "DRAFT",
                },
                metadata_json={
                    "document_count": len(prepared_documents),
                    "chunk_count": chunk_total,
                    "manifest_checksum": manifest.checksum,
                },
            )
        )
        session.flush()
        return ImportResult(
            kb_version_id=version.id,
            dataset_id=version.dataset_id,
            dataset_version=version.dataset_version,
            status=version.status,
            document_count=len(prepared_documents),
            chunk_count=chunk_total,
            no_op=False,
        )
