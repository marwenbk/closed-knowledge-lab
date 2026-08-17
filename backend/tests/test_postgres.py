from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from alembic import command
from app.config import get_settings
from app.db import engine_for_url
from app.kb import KnowledgeImportError, import_knowledge_base, load_manifest, word_count
from app.main import create_app
from app.models import AuditEvent, Chunk, Document, KnowledgeBaseVersion

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
MANIFEST_PATH = PROJECT_ROOT / "knowledge_base" / "manifest.json"


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
    try:
        admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
    except DBAPIError as exc:
        pytest.skip(f"PostgreSQL test database is unavailable: {exc}")

    test_url = url.set(database=database_name).render_as_string(hide_password=False)
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_url
    get_settings.cache_clear()
    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(alembic_config, "head")
    engine = create_engine(test_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()
        engine_for_url.cache_clear()
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
        admin_engine.dispose()


@pytest.mark.postgres
def test_migration_import_indexes_and_api(postgres_engine: Engine, tmp_path: Path) -> None:
    first = import_knowledge_base(postgres_engine, MANIFEST_PATH, activate=True)
    second = import_knowledge_base(postgres_engine, MANIFEST_PATH, activate=True)

    assert first.document_count == 15
    assert first.chunk_count == 30
    assert first.status == "ACTIVE"
    assert first.no_op is False
    assert second.no_op is True
    assert second.kb_version_id == first.kb_version_id

    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(KnowledgeBaseVersion)) == 1
        assert session.scalar(select(func.count()).select_from(Document)) == 15
        assert session.scalar(select(func.count()).select_from(Chunk)) == 30
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 2
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
        index_definitions = {row.indexdef.casefold() for row in index_rows}
        assert {"ix_chunks_search_vector", "ix_chunks_content_trgm"}.issubset(indexes)
        assert not any(
            "hnsw" in definition or "ivfflat" in definition for definition in index_definitions
        )
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

    application = create_app(postgres_engine)
    with TestClient(application, raise_server_exceptions=False) as client:
        ready_response = client.get("/ready")
        status_response = client.get("/api/v1/kb/status")

    assert ready_response.status_code == 200
    assert ready_response.json()["status"] == "ready"
    assert ready_response.json()["checks"]["semantic_index"] == {
        "status": "pending",
        "embedded_chunks": 0,
        "total_chunks": 30,
        "ann_index": False,
    }
    assert status_response.status_code == 200
    assert status_response.json()["document_count"] == 15
    assert status_response.json()["chunk_count"] == 30

    changed_manifest = copy_changed_same_version(tmp_path)
    with pytest.raises(KnowledgeImportError, match="different manifest checksum"):
        import_knowledge_base(postgres_engine, changed_manifest, activate=True)

    next_manifest = copy_knowledge_version(tmp_path, "2.0.1")
    next_version = import_knowledge_base(postgres_engine, next_manifest, activate=True)
    assert next_version.status == "ACTIVE"
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
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 4
