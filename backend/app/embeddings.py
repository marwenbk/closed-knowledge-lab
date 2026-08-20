from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from typing import Protocol
from uuid import UUID, uuid4

import numpy as np
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import HfHubHTTPError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from tokenizers import Tokenizer

from app.config import Settings
from app.models import AuditEvent, Chunk, KnowledgeBaseVersion

TOKENIZER_FILE = "tokenizer.json"


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    model_id: str
    model_version: str
    dimensions: int
    semantic_weight: float

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class ModelArtifacts:
    directory: Path
    model_path: Path
    tokenizer_path: Path


@dataclass(frozen=True)
class StaticModelArtifacts:
    directory: Path
    config_path: Path
    model_path: Path
    tokenizer_path: Path


@dataclass(frozen=True)
class EmbeddingRunResult:
    kb_version_id: UUID
    dataset_id: str
    dataset_version: str
    model_id: str
    model_version: str
    dimensions: int
    chunk_count: int
    embedded_count: int
    no_op: bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_embedding_vector(vector: Sequence[float], dimensions: int) -> None:
    if len(vector) != dimensions:
        raise EmbeddingError("Embedding provider returned the wrong dimensions")
    if not all(math.isfinite(value) for value in vector):
        raise EmbeddingError("Embedding provider returned a non-finite vector")
    squared_norm = math.fsum(value * value for value in vector)
    if not math.isfinite(squared_norm):
        raise EmbeddingError("Embedding provider returned a non-finite vector")
    if squared_norm <= 1e-24:
        raise EmbeddingError("Embedding provider returned a zero vector")


def _artifact_directory(settings: Settings) -> Path:
    model_slug = settings.embedding_model_id.replace("/", "--")
    return settings.embedding_cache_path / model_slug / settings.embedding_model_revision


def _validate_artifact(path: Path, expected_checksum: str, label: str) -> None:
    if not path.is_file():
        raise EmbeddingError(f"{label} is missing: {path}")
    actual_checksum = sha256_file(path)
    if actual_checksum != expected_checksum:
        raise EmbeddingError(
            f"{label} checksum mismatch: expected {expected_checksum}, got {actual_checksum}"
        )


def _download_artifact(
    *,
    repo_id: str,
    revision: str,
    filename: str,
    directory: Path,
) -> Path:
    error: Exception | None = None
    for attempt in range(3):
        try:
            return Path(
                hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    revision=revision,
                    local_dir=directory,
                    token=os.environ.get("HF_TOKEN"),
                    force_download=True,
                )
            )
        except (HfHubHTTPError, OSError, ValueError) as exc:
            error = exc
            if attempt < 2:
                sleep(2**attempt)
    raise EmbeddingError(f"Could not download pinned model artifact: {filename}") from error


def ensure_model_artifacts(settings: Settings, *, download: bool = False) -> ModelArtifacts:
    directory = _artifact_directory(settings)
    model_path = directory / settings.embedding_model_file
    tokenizer_path = directory / TOKENIZER_FILE

    def prepare(
        filename: str,
        path: Path,
        checksum: str,
        label: str,
    ) -> None:
        try:
            _validate_artifact(path, checksum, label)
            return
        except EmbeddingError:
            if not download:
                raise
        try:
            _download_artifact(
                repo_id=settings.embedding_model_id,
                revision=settings.embedding_model_revision,
                filename=filename,
                directory=directory,
            )
        except EmbeddingError as exc:
            raise EmbeddingError(
                f"Could not download {label} for {settings.embedding_model_id} "
                f"at {settings.embedding_model_revision}: {exc}"
            ) from exc
        _validate_artifact(path, checksum, label)

    prepare(
        settings.embedding_model_file,
        model_path,
        settings.embedding_model_sha256,
        "embedding model",
    )
    prepare(
        TOKENIZER_FILE,
        tokenizer_path,
        settings.embedding_tokenizer_sha256,
        "tokenizer",
    )
    return ModelArtifacts(directory, model_path, tokenizer_path)


def ensure_static_model_artifacts(
    settings: Settings, *, download: bool = False
) -> StaticModelArtifacts:
    directory = (
        settings.embedding_cache_path
        / settings.static_embedding_model_id.replace("/", "--")
        / settings.static_embedding_model_revision
    )
    files = (
        ("config.json", settings.static_embedding_config_sha256, "static model config"),
        ("model.safetensors", settings.static_embedding_model_sha256, "static model"),
        ("tokenizer.json", settings.static_embedding_tokenizer_sha256, "static tokenizer"),
    )
    for filename, checksum, label in files:
        path = directory / filename
        try:
            _validate_artifact(path, checksum, label)
            continue
        except EmbeddingError:
            if not download:
                raise
        _download_artifact(
            repo_id=settings.static_embedding_model_id,
            revision=settings.static_embedding_model_revision,
            filename=filename,
            directory=directory,
        )
        _validate_artifact(path, checksum, label)
    return StaticModelArtifacts(
        directory=directory,
        config_path=directory / "config.json",
        model_path=directory / "model.safetensors",
        tokenizer_path=directory / "tokenizer.json",
    )


def mean_pool(last_hidden_state: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    if last_hidden_state.ndim != 3 or attention_mask.ndim != 2:
        raise EmbeddingError("Unexpected embedding model output shape")
    expanded_mask = attention_mask[..., np.newaxis].astype(np.float32)
    token_sums = (last_hidden_state * expanded_mask).sum(axis=1)
    token_counts = np.clip(expanded_mask.sum(axis=1), 1e-9, None)
    pooled = token_sums / token_counts
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    return pooled / np.clip(norms, 1e-12, None)


class OnnxE5EmbeddingProvider:
    semantic_weight = 1.0

    def __init__(self, settings: Settings, *, download: bool = False) -> None:
        artifacts = ensure_model_artifacts(settings, download=download)
        self.model_id = settings.embedding_model_id
        self.model_version = settings.embedding_model_revision
        self.dimensions = settings.embedding_dimensions
        self.max_length = settings.embedding_max_length
        try:
            import onnxruntime as ort  # type: ignore[import-untyped]

            self.tokenizer = Tokenizer.from_file(str(artifacts.tokenizer_path))
            self.tokenizer.enable_truncation(max_length=self.max_length)
            session_options = ort.SessionOptions()
            session_options.enable_cpu_mem_arena = False
            session_options.enable_mem_pattern = False
            session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            session_options.intra_op_num_threads = 1
            session_options.inter_op_num_threads = 1
            session_options.add_session_config_entry("session.disable_prepacking", "1")
            self.session = ort.InferenceSession(
                str(artifacts.model_path),
                sess_options=session_options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:
            raise EmbeddingError(
                f"Could not load embedding model from {artifacts.directory}"
            ) from exc
        self.input_names = {item.name for item in self.session.get_inputs()}
        required_inputs = {"input_ids", "attention_mask"}
        if not required_inputs.issubset(self.input_names):
            raise EmbeddingError("Embedding model does not expose the required inputs")
        self.pad_id = self.tokenizer.token_to_id("<pad>")
        if self.pad_id is None:
            raise EmbeddingError("Embedding tokenizer does not define a <pad> token")

    def _embed(self, texts: Sequence[str], prefix: str) -> list[list[float]]:
        if not texts:
            return []
        if any(not text.strip() for text in texts):
            raise EmbeddingError("Embedding inputs must be non-empty")
        encodings = self.tokenizer.encode_batch([f"{prefix}: {text.strip()}" for text in texts])
        width = max(len(encoding.ids) for encoding in encodings)
        input_ids = np.full((len(encodings), width), self.pad_id, dtype=np.int64)
        attention_mask = np.zeros((len(encodings), width), dtype=np.int64)
        token_type_ids = np.zeros((len(encodings), width), dtype=np.int64)
        for row, encoding in enumerate(encodings):
            length = len(encoding.ids)
            input_ids[row, :length] = encoding.ids
            attention_mask[row, :length] = encoding.attention_mask
            token_type_ids[row, :length] = encoding.type_ids

        feed: dict[str, np.ndarray] = {}
        if "input_ids" in self.input_names:
            feed["input_ids"] = input_ids
        if "attention_mask" in self.input_names:
            feed["attention_mask"] = attention_mask
        if "token_type_ids" in self.input_names:
            feed["token_type_ids"] = token_type_ids
        try:
            outputs = self.session.run(None, feed)
        except Exception as exc:
            raise EmbeddingError("Embedding inference failed") from exc
        if not outputs:
            raise EmbeddingError("Embedding model returned no output")
        model_output = np.asarray(outputs[0], dtype=np.float32)
        pooled = mean_pool(model_output, attention_mask) if model_output.ndim == 3 else model_output
        if pooled.ndim != 2 or pooled.shape[1] != self.dimensions:
            raise EmbeddingError(
                f"Embedding model returned {pooled.shape}, expected (*, {self.dimensions})"
            )
        if model_output.ndim == 2:
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            pooled = pooled / np.clip(norms, 1e-12, None)
        return pooled.astype(np.float32).tolist()

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "passage")

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "query")


class StaticE5EmbeddingProvider:
    semantic_weight = 0.001

    def __init__(self, settings: Settings, *, download: bool = False) -> None:
        from safetensors.numpy import load_file

        artifacts = ensure_static_model_artifacts(settings, download=download)
        self.model_id = settings.static_embedding_model_id
        self.model_version = settings.static_embedding_model_revision
        self.dimensions = settings.embedding_dimensions
        self.max_length = settings.embedding_max_length
        try:
            self.tokenizer = Tokenizer.from_file(str(artifacts.tokenizer_path))
            self.tokenizer.enable_truncation(max_length=self.max_length)
            self.embeddings = load_file(artifacts.model_path)["embeddings"]
        except Exception as exc:
            raise EmbeddingError(
                f"Could not load static embedding model from {artifacts.directory}"
            ) from exc
        if (
            self.embeddings.ndim != 2
            or self.embeddings.shape[0] != self.tokenizer.get_vocab_size()
            or self.embeddings.shape[1] > self.dimensions
        ):
            raise EmbeddingError("Static embedding model returned an invalid shape")
        unknown_token = getattr(self.tokenizer.model, "unk_token", None)
        self.unknown_token_id = (
            self.tokenizer.token_to_id(unknown_token) if unknown_token is not None else None
        )

    def _embed(self, texts: Sequence[str], prefix: str) -> list[list[float]]:
        if not texts:
            return []
        if any(not text.strip() for text in texts):
            raise EmbeddingError("Embedding inputs must be non-empty")
        encodings = self.tokenizer.encode_batch(
            [f"{prefix}: {text.strip()}" for text in texts], add_special_tokens=False
        )
        pooled = []
        for encoding in encodings:
            token_ids = [token_id for token_id in encoding.ids if token_id != self.unknown_token_id]
            pooled.append(
                self.embeddings[token_ids].mean(axis=0)
                if token_ids
                else np.zeros(self.embeddings.shape[1], dtype=np.float32)
            )
        vectors = np.stack(pooled).astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / np.clip(norms, 1e-12, None)
        if vectors.shape[1] < self.dimensions:
            vectors = np.pad(vectors, ((0, 0), (0, self.dimensions - vectors.shape[1])))
        return vectors.astype(np.float32).tolist()

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "passage")

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "query")


def configured_embedding_provider(
    settings: Settings, *, download: bool = False
) -> EmbeddingProvider:
    if settings.embedding_provider == "static":
        return StaticE5EmbeddingProvider(settings, download=download)
    return OnnxE5EmbeddingProvider(settings, download=download)


def ensure_configured_model_artifacts(settings: Settings) -> None:
    if settings.embedding_provider == "static":
        ensure_static_model_artifacts(settings, download=True)
    else:
        ensure_model_artifacts(settings, download=True)


def _embedding_is_current(chunk: Chunk, provider: EmbeddingProvider) -> bool:
    return (
        chunk.embedding is not None
        and chunk.embedding_model == provider.model_id
        and chunk.embedding_version == provider.model_version
        and chunk.embedding_dimensions == provider.dimensions
        and chunk.embedding_content_checksum == chunk.content_checksum
    )


def embed_knowledge_base(
    engine: Engine,
    provider: EmbeddingProvider,
    *,
    batch_size: int,
    dataset_id: str,
    dataset_version: str,
    expected_manifest_checksum: str | None = None,
    actor_type: str = "SYSTEM",
    actor_id: str | None = None,
    request_id: UUID | None = None,
) -> EmbeddingRunResult:
    if batch_size <= 0:
        raise EmbeddingError("Embedding batch size must be positive")
    if provider.dimensions != 384:
        raise EmbeddingError("The current database schema requires 384-dimensional embeddings")

    with Session(engine) as session, session.begin():
        version = session.scalar(
            select(KnowledgeBaseVersion)
            .where(
                KnowledgeBaseVersion.dataset_id == dataset_id,
                KnowledgeBaseVersion.dataset_version == dataset_version,
            )
            .with_for_update()
        )
        if version is None:
            raise EmbeddingError(
                f"Knowledge-base version is not loaded: {dataset_id}:{dataset_version}"
            )
        if (
            expected_manifest_checksum is not None
            and version.manifest_checksum != expected_manifest_checksum
        ):
            raise EmbeddingError("The knowledge-base draft changed before indexing started")
        chunks = list(
            session.scalars(
                select(Chunk)
                .where(Chunk.kb_version_id == version.id)
                .order_by(Chunk.stable_chunk_key)
            )
        )
        if not chunks:
            raise EmbeddingError("The knowledge-base version contains no chunks")
        stale = [chunk for chunk in chunks if not _embedding_is_current(chunk, provider)]
        if not stale:
            return EmbeddingRunResult(
                kb_version_id=version.id,
                dataset_id=version.dataset_id,
                dataset_version=version.dataset_version,
                model_id=provider.model_id,
                model_version=provider.model_version,
                dimensions=provider.dimensions,
                chunk_count=len(chunks),
                embedded_count=0,
                no_op=True,
            )
        if version.status != "DRAFT":
            raise EmbeddingError(
                "An active or retired knowledge-base version is immutable; import a new draft"
            )
        embedded_at = datetime.now(UTC)
        for offset in range(0, len(stale), batch_size):
            batch = stale[offset : offset + batch_size]
            vectors = provider.embed_documents([chunk.content for chunk in batch])
            if len(vectors) != len(batch):
                raise EmbeddingError("Embedding provider returned the wrong batch size")
            for chunk, vector in zip(batch, vectors, strict=True):
                validate_embedding_vector(vector, provider.dimensions)
                chunk.embedding = vector
                chunk.embedding_model = provider.model_id
                chunk.embedding_version = provider.model_version
                chunk.embedding_dimensions = provider.dimensions
                chunk.embedding_content_checksum = chunk.content_checksum
                chunk.embedded_at = embedded_at
        session.add(
            AuditEvent(
                id=uuid4(),
                event_type="knowledge_base.embedded",
                actor_type=actor_type,
                actor_id=actor_id,
                resource_type="knowledge_base_version",
                resource_id=str(version.id),
                request_id=request_id,
                before_json=None,
                after_json={
                    "embedding_model": provider.model_id,
                    "embedding_version": provider.model_version,
                    "embedding_dimensions": provider.dimensions,
                },
                metadata_json={
                    "chunk_count": len(chunks),
                    "embedded_count": len(stale),
                },
            )
        )
        session.flush()
        return EmbeddingRunResult(
            kb_version_id=version.id,
            dataset_id=version.dataset_id,
            dataset_version=version.dataset_version,
            model_id=provider.model_id,
            model_version=provider.model_version,
            dimensions=provider.dimensions,
            chunk_count=len(chunks),
            embedded_count=len(stale),
            no_op=False,
        )
