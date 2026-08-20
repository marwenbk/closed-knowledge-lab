from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
import pytest
from app.config import Settings, get_settings
from app.embeddings import (
    EmbeddingError,
    OnnxE5EmbeddingProvider,
    StaticE5EmbeddingProvider,
    ensure_configured_model_artifacts,
    ensure_model_artifacts,
    mean_pool,
)
from app.kb import prepare_dataset

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "knowledge_base" / "manifest.json"


def test_mean_pool_ignores_padding_and_normalizes_vectors() -> None:
    hidden = np.asarray(
        [
            [[1.0, 0.0], [3.0, 0.0], [9.0, 9.0]],
            [[0.0, 2.0], [0.0, 4.0], [7.0, 7.0]],
        ],
        dtype=np.float32,
    )
    mask = np.asarray([[1, 1, 0], [1, 1, 0]], dtype=np.int64)

    pooled = mean_pool(hidden, mask)

    np.testing.assert_allclose(pooled, [[1.0, 0.0], [0.0, 1.0]])
    np.testing.assert_allclose(np.linalg.norm(pooled, axis=1), [1.0, 1.0])


def test_model_artifacts_are_checksum_validated(tmp_path: Path) -> None:
    model_content = b"synthetic onnx model"
    tokenizer_content = b'{"version":"1.0"}'
    settings = Settings(
        _env_file=None,
        embedding_model_id="example/test-model",
        embedding_model_revision="a" * 40,
        embedding_model_file="model.onnx",
        embedding_model_sha256=hashlib.sha256(model_content).hexdigest(),
        embedding_tokenizer_sha256=hashlib.sha256(tokenizer_content).hexdigest(),
        embedding_cache_dir=tmp_path,
    )
    directory = tmp_path / "example--test-model" / ("a" * 40)
    directory.mkdir(parents=True)
    (directory / "model.onnx").write_bytes(model_content)
    (directory / "tokenizer.json").write_bytes(tokenizer_content)

    artifacts = ensure_model_artifacts(settings)
    assert artifacts.model_path == directory / "model.onnx"

    (directory / "tokenizer.json").write_bytes(b"tampered")
    with pytest.raises(EmbeddingError, match="tokenizer checksum mismatch"):
        ensure_model_artifacts(settings)


@pytest.mark.model
def test_pinned_multilingual_e5_small_model_runs_locally() -> None:
    settings = get_settings()
    try:
        ensure_model_artifacts(settings)
    except EmbeddingError as exc:
        if "is missing" in str(exc):
            if os.environ.get("TOPMED_REQUIRE_MODEL_TESTS") == "1":
                pytest.fail(f"Pinned embedding model is required but unavailable: {exc}")
            pytest.skip("Pinned embedding model has not been downloaded by backend setup")
        raise

    provider = OnnxE5EmbeddingProvider(settings)
    query = provider.embed_queries(["quantos dependentes o plano Família permite?"])[0]
    passages = provider.embed_documents(
        [
            "O plano Família permite o cadastro de até 3 dependentes.",
            "O reembolso retorna ao método de pagamento original.",
        ]
    )

    assert len(query) == settings.embedding_dimensions
    assert all(len(vector) == settings.embedding_dimensions for vector in passages)
    assert np.linalg.norm(query) == pytest.approx(1.0, abs=1e-5)
    assert float(np.dot(query, passages[0])) > float(np.dot(query, passages[1]))

    _, documents = prepare_dataset(MANIFEST_PATH)
    encoded_lengths = [
        len(provider.tokenizer.encode(f"passage: {chunk.content}").ids)
        for document in documents
        for chunk in document.chunks
    ]
    assert max(encoded_lengths) <= settings.embedding_max_length


@pytest.mark.model
def test_pinned_static_distillation_runs_with_physical_vector_compatibility() -> None:
    settings = Settings(_env_file=None, embedding_provider="static")
    try:
        ensure_configured_model_artifacts(settings)
    except EmbeddingError as exc:
        if "is missing" in str(exc):
            if os.environ.get("TOPMED_REQUIRE_MODEL_TESTS") == "1":
                pytest.fail(f"Pinned static model is required but unavailable: {exc}")
            pytest.skip("Pinned static model has not been downloaded")
        raise

    provider = StaticE5EmbeddingProvider(settings)
    query = provider.embed_queries(["quantos dependentes o plano Família permite?"])[0]
    passages = provider.embed_documents(
        [
            "O plano Família permite o cadastro de até 3 dependentes.",
            "O reembolso retorna ao método de pagamento original.",
        ]
    )

    assert provider.model_id == settings.static_embedding_model_id
    assert len(query) == settings.embedding_dimensions
    assert all(len(vector) == settings.embedding_dimensions for vector in passages)
    assert np.linalg.norm(query) == pytest.approx(1.0, abs=1e-5)
    assert float(np.dot(query, passages[0])) > float(np.dot(query, passages[1]))
