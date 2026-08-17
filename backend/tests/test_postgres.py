from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.config import get_settings
from app.db import engine_for_url
from app.embeddings import EmbeddingError, EmbeddingRunResult, embed_knowledge_base
from app.kb import (
    ActivationResult,
    KnowledgeImportError,
    activate_knowledge_base,
    import_knowledge_base,
    load_manifest,
    word_count,
)
from app.main import create_app
from app.models import AuditEvent, Chunk, Document, KnowledgeBaseVersion
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
MANIFEST_PATH = PROJECT_ROOT / "knowledge_base" / "manifest.json"


class FakeEmbeddingProvider:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        settings = get_settings()
        self.model_id = settings.embedding_model_id
        self.model_version = settings.embedding_model_revision
        self.dimensions = settings.embedding_dimensions
        self.fail_on_call = fail_on_call
        self.document_calls = 0

    def _vector(self, text_value: str) -> list[float]:
        digest = hashlib.sha256(text_value.encode()).digest()
        values = [(byte - 127.5) / 127.5 for byte in digest * 12]
        norm = math.sqrt(sum(value * value for value in values))
        return [value / norm for value in values]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls += 1
        if self.document_calls == self.fail_on_call:
            raise EmbeddingError("synthetic embedding failure")
        return [self._vector(f"passage: {text_value}") for text_value in texts]

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(f"query: {text_value}") for text_value in texts]


def embed_version(
    engine: Engine,
    provider: FakeEmbeddingProvider,
    version: str = "2.0.0",
) -> EmbeddingRunResult:
    settings = get_settings()
    return embed_knowledge_base(
        engine,
        provider,
        batch_size=settings.embedding_batch_size,
        dataset_id=settings.expected_dataset_id,
        dataset_version=version,
    )


def activate_version(engine: Engine, version: str = "2.0.0") -> ActivationResult:
    settings = get_settings()
    return activate_knowledge_base(
        engine,
        dataset_id=settings.expected_dataset_id,
        dataset_version=version,
        expected_embedding_model=settings.embedding_model_id,
        expected_embedding_version=settings.embedding_model_revision,
        expected_embedding_dimensions=settings.embedding_dimensions,
    )


def copy_knowledge_version(tmp_path: Path, version: str) -> Path:
    copied_kb = tmp_path / f"knowledge_base_{version}"
    shutil.copytree(MANIFEST_PATH.parent, copied_kb)
    manifest_path = copied_kb / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset_version"] = version
    for document in manifest["documents"]:
        path = copied_kb / document["path"]
        content = path.read_text(encoding="utf-8").replace(
            'dataset_version: "2.0.0"', f'dataset_version: "{version}"', 1
        )
        path.write_text(content, encoding="utf-8")
        document["sha256"] = hashlib.sha256(content.encode()).hexdigest()
        document["word_count"] = word_count(content)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest_path


def copy_changed_same_version(tmp_path: Path) -> Path:
    copied_kb = tmp_path / "knowledge_base_changed"
    shutil.copytree(MANIFEST_PATH.parent, copied_kb)
    manifest_path = copied_kb / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    document = manifest["documents"][0]
    path = copied_kb / document["path"]
    content = path.read_text(encoding="utf-8") + "\nConteúdo sintético alterado.\n"
    path.write_text(content, encoding="utf-8")
    document["sha256"] = hashlib.sha256(content.encode()).hexdigest()
    document["word_count"] = word_count(content)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest_path


@pytest.fixture
def postgres_engine() -> Iterator[Engine]:
    maintenance_url = os.environ.get("TOPMED_TEST_DATABASE_URL")
    if not maintenance_url:
        pytest.skip("TOPMED_TEST_DATABASE_URL is not configured")
    url = make_url(maintenance_url)
    database_name = f"topmed_test_{uuid4().hex}"
    admin_url = url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    test_engine: Engine | None = None
    database_created = False
    previous_database_url = os.environ.get("DATABASE_URL")
    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
        database_created = True
        test_url = url.set(database=database_name).render_as_string(hide_password=False)
        os.environ["DATABASE_URL"] = test_url
        get_settings.cache_clear()
        alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
        command.upgrade(alembic_config, "head")
        test_engine = create_engine(test_url, pool_pre_ping=True)
        yield test_engine
    finally:
        if test_engine is not None:
            test_engine.dispose()
        engine_for_url.cache_clear()
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        get_settings.cache_clear()
        if database_created:
            with admin_engine.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
        admin_engine.dispose()


@pytest.mark.postgres
def test_migration_import_indexes_and_api(postgres_engine: Engine, tmp_path: Path) -> None:
    first = import_knowledge_base(postgres_engine, MANIFEST_PATH)
    second = import_knowledge_base(postgres_engine, MANIFEST_PATH)

    assert first.document_count == 15
    assert first.chunk_count == 30
    assert first.status == "DRAFT"
    assert first.no_op is False
    assert second.no_op is True
    assert second.kb_version_id == first.kb_version_id

    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(KnowledgeBaseVersion)) == 1
        assert session.scalar(select(func.count()).select_from(Document)) == 15
        assert session.scalar(select(func.count()).select_from(Chunk)) == 30
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
        extensions = set(
            session.execute(
                text("SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pg_trgm')")
            ).scalars()
        )
        assert extensions == {"vector", "pg_trgm"}
        index_rows = session.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'chunks'")
        ).all()
        indexes = {row.indexname for row in index_rows}
        assert {"ix_chunks_search_vector", "ix_chunks_content_trgm"}.issubset(indexes)
        assert "ix_chunks_embedding_hnsw" not in indexes
        lexical_matches = session.scalar(
            text(
                "SELECT count(*) FROM chunks "
                "WHERE search_vector @@ plainto_tsquery('portuguese', 'dependentes')"
            )
        )
        typo_matches = session.scalar(
            text(
                "SELECT count(*) FROM chunks "
                "WHERE word_similarity('dependntes', content_normalized) > 0.6"
            )
        )
        assert int(lexical_matches or 0) > 0
        assert int(typo_matches or 0) > 0

        session.execute(text("SET LOCAL enable_seqscan = off"))
        session.execute(text("SELECT set_config('pg_trgm.word_similarity_threshold', '0.3', true)"))
        trigram_plan = "\n".join(
            row[0]
            for row in session.execute(
                text("EXPLAIN SELECT id FROM chunks WHERE 'dependntes' <% content_normalized")
            )
        )
        assert "ix_chunks_content_trgm" in trigram_plan

        audit = session.scalar(select(AuditEvent).limit(1))
        assert audit is not None
        with pytest.raises(DBAPIError, match="append-only"), session.begin_nested():
            session.execute(
                text("UPDATE audit_events SET event_type = 'changed' WHERE id = :id"),
                {"id": audit.id},
            )

        canonical_manifest = load_manifest(MANIFEST_PATH)
        expected_checksums = {
            document.document_id: document.sha256 for document in canonical_manifest.documents
        }
        imported_documents = session.scalars(select(Document)).all()
        assert {
            document.document_key: document.checksum for document in imported_documents
        } == expected_checksums
        imported_chunks = session.scalars(select(Chunk)).all()
        assert all(
            chunk.metadata_json["source_path"].startswith("knowledge_base/")
            for chunk in imported_chunks
        )
        assert all(chunk.embedding is None for chunk in imported_chunks)

    application = create_app(postgres_engine, FakeEmbeddingProvider())
    with TestClient(application, raise_server_exceptions=False) as client:
        ready_response = client.get("/ready")
        status_response = client.get("/api/v1/kb/status")

    assert ready_response.status_code == 503
    assert ready_response.json()["status"] == "not_ready"
    semantic_check = ready_response.json()["checks"]["semantic_index"]
    assert semantic_check["status"] == "pending"
    assert semantic_check.get("embedded_chunks", 0) == 0
    assert semantic_check.get("ann_index", False) is False
    assert status_response.status_code == 503

    with pytest.raises(KnowledgeImportError, match="complete, current embedding index"):
        activate_version(postgres_engine)

    with pytest.raises(EmbeddingError, match="synthetic embedding failure"):
        embed_version(postgres_engine, FakeEmbeddingProvider(fail_on_call=2))
    with Session(postgres_engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.embedding.is_not(None))
            )
            == 0
        )
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1

    provider = FakeEmbeddingProvider()
    embedded = embed_version(postgres_engine, provider)
    repeated = embed_version(postgres_engine, provider)
    assert embedded.embedded_count == 30
    assert embedded.no_op is False
    assert repeated.embedded_count == 0
    assert repeated.no_op is True

    with Session(postgres_engine) as session:
        embedded_chunks = session.scalars(select(Chunk)).all()
        assert all(chunk.embedding is not None for chunk in embedded_chunks)
        assert all(chunk.embedding_model == provider.model_id for chunk in embedded_chunks)
        assert all(chunk.embedding_version == provider.model_version for chunk in embedded_chunks)
        assert all(chunk.embedding_dimensions == provider.dimensions for chunk in embedded_chunks)
        assert all(
            chunk.embedding_content_checksum == chunk.content_checksum for chunk in embedded_chunks
        )
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 2

    activation = activate_version(postgres_engine)
    repeated_activation = activate_version(postgres_engine)
    assert activation.no_op is False
    assert repeated_activation.no_op is True

    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 3

    application = create_app(postgres_engine, provider)
    with TestClient(application, raise_server_exceptions=False) as client:
        ready_response = client.get("/ready")
        policy_response = client.post(
            "/api/v1/kb/retrieve", json={"query": "Qual é a regra TM-REF-014?"}
        )
        second_hop_response = client.post(
            "/api/v1/kb/retrieve",
            json={"query": "Quantos dependentes o nível Gold permite?"},
        )
        platinum_response = client.post(
            "/api/v1/kb/retrieve",
            json={"query": "O nível Platinum cobre psicologia e em quais horários?"},
        )
        typo_response = client.post(
            "/api/v1/kb/retrieve",
            json={"query": "quantos depedentes o plano famlia aceita"},
        )

    assert ready_response.status_code == 200
    assert ready_response.json()["status"] == "ready"
    assert ready_response.json()["checks"]["semantic_index"]["status"] == "ready"
    assert policy_response.status_code == 200
    assert "refund-policy" in {match["document_key"] for match in policy_response.json()["matches"]}
    assert second_hop_response.status_code == 200
    assert second_hop_response.json()["second_hop"]["used"] is True
    second_hop_documents = {
        match["document_key"] for match in second_hop_response.json()["matches"]
    }
    assert {"employer-plans", "family-members"}.issubset(second_hop_documents)
    assert platinum_response.status_code == 200
    assert platinum_response.json()["second_hop"]["used"] is True
    platinum_documents = {match["document_key"] for match in platinum_response.json()["matches"]}
    assert {"employer-plans", "specialties", "consultation-hours"}.issubset(platinum_documents)
    assert typo_response.status_code == 200
    assert typo_response.json()["trigram_fallback_used"] is True
    assert "family-members" in {match["document_key"] for match in typo_response.json()["matches"]}

    changed_manifest = copy_changed_same_version(tmp_path)
    with pytest.raises(KnowledgeImportError, match="different manifest checksum"):
        import_knowledge_base(postgres_engine, changed_manifest)

    next_manifest = copy_knowledge_version(tmp_path, "2.0.1")
    next_version = import_knowledge_base(postgres_engine, next_manifest)
    assert next_version.status == "DRAFT"
    with Session(postgres_engine) as session:
        states = dict(
            session.execute(
                select(
                    KnowledgeBaseVersion.dataset_version,
                    KnowledgeBaseVersion.status,
                )
            ).all()
        )
        assert states == {"2.0.0": "ACTIVE", "2.0.1": "DRAFT"}

        old_document_id = session.scalar(
            select(Document.id).where(Document.kb_version_id == first.kb_version_id).limit(1)
        )
        new_chunk_id = session.scalar(
            select(Chunk.id).where(Chunk.kb_version_id == next_version.kb_version_id).limit(1)
        )
        assert old_document_id is not None and new_chunk_id is not None
        with pytest.raises(DBAPIError), session.begin_nested():
            session.execute(
                text("UPDATE chunks SET document_id = :document_id WHERE id = :chunk_id"),
                {"document_id": old_document_id, "chunk_id": new_chunk_id},
            )

    with pytest.raises(KnowledgeImportError, match="complete, current embedding index"):
        activate_version(postgres_engine, "2.0.1")
    embed_version(postgres_engine, provider, "2.0.1")
    next_activation = activate_version(postgres_engine, "2.0.1")
    assert next_activation.previous_version_id == first.kb_version_id

    with Session(postgres_engine) as session:
        states = dict(
            session.execute(
                select(
                    KnowledgeBaseVersion.dataset_version,
                    KnowledgeBaseVersion.status,
                )
            ).all()
        )
        assert states == {"2.0.0": "RETIRED", "2.0.1": "ACTIVE"}
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 6

    application = create_app(postgres_engine, provider)
    with TestClient(application, raise_server_exceptions=False) as client:
        active_response = client.post(
            "/api/v1/kb/retrieve", json={"query": "Qual é a regra TM-REF-014?"}
        )
    assert active_response.status_code == 200
    assert active_response.json()["dataset_version"] == "2.0.1"
