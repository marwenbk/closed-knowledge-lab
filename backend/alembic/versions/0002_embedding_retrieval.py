"""Add fixed-dimension embeddings and provenance constraints.

Revision ID: 0002_embedding_retrieval
Revises: 0001_knowledge_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR

revision: str = "0002_embedding_retrieval"
down_revision: str | None = "0001_knowledge_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "chunks",
        "embedding",
        existing_type=VECTOR(),
        type_=VECTOR(384),
        existing_nullable=True,
        postgresql_using="embedding::vector(384)",
    )
    op.add_column(
        "chunks",
        sa.Column(
            "content_checksum",
            sa.String(length=64),
            sa.Computed(
                "encode(sha256(content_normalized::bytea), 'hex')",
                persisted=True,
            ),
            nullable=False,
        ),
    )
    op.add_column("chunks", sa.Column("embedding_dimensions", sa.Integer(), nullable=True))
    op.add_column(
        "chunks",
        sa.Column("embedding_content_checksum", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "chunks",
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE chunks SET embedding = NULL, embedding_model = NULL, embedding_version = NULL"
    )
    op.create_check_constraint(
        "ck_chunks_embedding_metadata_complete",
        "chunks",
        "(embedding IS NULL AND embedding_model IS NULL AND embedding_version IS NULL "
        "AND embedding_dimensions IS NULL AND embedding_content_checksum IS NULL "
        "AND embedded_at IS NULL) OR "
        "(embedding IS NOT NULL AND embedding_model IS NOT NULL AND embedding_version IS NOT NULL "
        "AND embedding_dimensions = 384 AND embedding_content_checksum = content_checksum "
        "AND embedded_at IS NOT NULL)",
    )
    op.create_unique_constraint(
        "uq_documents_id_version",
        "documents",
        ["id", "kb_version_id"],
    )
    op.create_unique_constraint(
        "uq_document_revisions_id_document",
        "document_revisions",
        ["id", "document_id"],
    )
    op.create_foreign_key(
        "fk_chunks_document_version",
        "chunks",
        "documents",
        ["document_id", "kb_version_id"],
        ["id", "kb_version_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_chunks_revision_document",
        "chunks",
        "document_revisions",
        ["revision_id", "document_id"],
        ["id", "document_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    for table, name, constraint_type in (
        ("chunks", "fk_chunks_revision_document", "foreignkey"),
        ("chunks", "fk_chunks_document_version", "foreignkey"),
        ("document_revisions", "uq_document_revisions_id_document", "unique"),
        ("documents", "uq_documents_id_version", "unique"),
        ("chunks", "ck_chunks_embedding_metadata_complete", "check"),
    ):
        op.drop_constraint(name, table, type_=constraint_type, if_exists=True)
    op.drop_index("ix_chunks_embedding_hnsw", table_name="chunks", if_exists=True)
    for column in (
        "embedded_at",
        "embedding_content_checksum",
        "embedding_dimensions",
        "content_checksum",
    ):
        op.drop_column("chunks", column, if_exists=True)
    op.alter_column(
        "chunks",
        "embedding",
        existing_type=VECTOR(384),
        type_=VECTOR(),
        existing_nullable=True,
        postgresql_using="embedding::vector",
    )
