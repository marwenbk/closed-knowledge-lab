"""Add governed knowledge drafting and publication.

Revision ID: 0006_knowledge_publishing
Revises: 0005_admin_insights
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_knowledge_publishing"
down_revision: str | None = "0005_admin_insights"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("knowledge_base_versions", sa.Column("source_version_id", sa.Uuid()))
    op.add_column("knowledge_base_versions", sa.Column("created_by", sa.Uuid()))
    op.add_column("knowledge_base_versions", sa.Column("activated_by", sa.Uuid()))
    op.add_column(
        "knowledge_base_versions",
        sa.Column("validated_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "knowledge_base_versions",
        sa.Column("validated_checksum", sa.String(length=64)),
    )
    op.add_column(
        "knowledge_base_versions",
        sa.Column("validation_report_json", postgresql.JSONB(astext_type=sa.Text())),
    )
    op.create_foreign_key(
        "fk_kb_versions_source",
        "knowledge_base_versions",
        "knowledge_base_versions",
        ["source_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_kb_versions_created_by",
        "knowledge_base_versions",
        "admin_users",
        ["created_by"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_kb_versions_activated_by",
        "knowledge_base_versions",
        "admin_users",
        ["activated_by"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_kb_versions_dataset_created_at",
        "knowledge_base_versions",
        ["dataset_id", "created_at"],
    )

    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("suite", sa.String(length=100), nullable=False),
        sa.Column("kb_version_id", sa.Uuid(), nullable=False),
        sa.Column("kb_manifest_checksum", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("settings_version", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "metrics_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("started_by", sa.Uuid(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'PASSED', 'FAILED')",
            name="ck_evaluation_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["kb_version_id"],
            ["knowledge_base_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["started_by"], ["admin_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evaluation_runs_version_started_at",
        "evaluation_runs",
        ["kb_version_id", "started_at"],
    )
    op.create_index(
        "uq_evaluation_runs_one_running_version",
        "evaluation_runs",
        ["kb_version_id"],
        unique=True,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )

    op.execute(
        """
        CREATE FUNCTION enforce_knowledge_draft_mutation() RETURNS trigger AS $$
        DECLARE
            version_id uuid;
            version_status text;
        BEGIN
            IF TG_TABLE_NAME = 'documents' THEN
                version_id := COALESCE(NEW.kb_version_id, OLD.kb_version_id);
            ELSIF TG_TABLE_NAME = 'chunks' THEN
                version_id := COALESCE(NEW.kb_version_id, OLD.kb_version_id);
            ELSE
                SELECT kb_version_id INTO version_id
                FROM documents
                WHERE id = COALESCE(NEW.document_id, OLD.document_id);
            END IF;

            SELECT status INTO version_status
            FROM knowledge_base_versions
            WHERE id = version_id;
            IF version_status <> 'DRAFT' THEN
                RAISE EXCEPTION 'knowledge content is immutable outside a draft version';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in ("documents", "document_revisions", "chunks"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_draft_mutation
            BEFORE INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION enforce_knowledge_draft_mutation()
            """
        )
    op.execute(
        """
        CREATE FUNCTION enforce_knowledge_version_immutability() RETURNS trigger AS $$
        BEGIN
            IF OLD.status IN ('ACTIVE', 'RETIRED') AND (
                NEW.dataset_id,
                NEW.dataset_version,
                NEW.generator_version,
                NEW.language,
                NEW.seed_checksum,
                NEW.template_checksum,
                NEW.manifest_checksum,
                NEW.source_version_id,
                NEW.created_by,
                NEW.validated_at,
                NEW.validated_checksum,
                NEW.validation_report_json,
                NEW.created_at
            ) IS DISTINCT FROM (
                OLD.dataset_id,
                OLD.dataset_version,
                OLD.generator_version,
                OLD.language,
                OLD.seed_checksum,
                OLD.template_checksum,
                OLD.manifest_checksum,
                OLD.source_version_id,
                OLD.created_by,
                OLD.validated_at,
                OLD.validated_checksum,
                OLD.validation_report_json,
                OLD.created_at
            ) THEN
                RAISE EXCEPTION 'active and retired knowledge versions are immutable';
            END IF;
            IF OLD.status = 'ACTIVE' AND NEW.status NOT IN ('ACTIVE', 'RETIRED') THEN
                RAISE EXCEPTION 'an active knowledge version can only be retired';
            END IF;
            IF OLD.status = 'RETIRED' AND NEW.status NOT IN ('RETIRED', 'ACTIVE') THEN
                RAISE EXCEPTION 'a retired knowledge version can only be reactivated';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER knowledge_base_versions_immutable
        BEFORE UPDATE ON knowledge_base_versions
        FOR EACH ROW EXECUTE FUNCTION enforce_knowledge_version_immutability()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS knowledge_base_versions_immutable ON knowledge_base_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_knowledge_version_immutability()")
    for table in ("chunks", "document_revisions", "documents"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_draft_mutation ON {table}")
    op.execute("DROP FUNCTION IF EXISTS enforce_knowledge_draft_mutation()")
    op.drop_index("uq_evaluation_runs_one_running_version", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_version_started_at", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
    op.drop_index("ix_kb_versions_dataset_created_at", table_name="knowledge_base_versions")
    op.drop_constraint("fk_kb_versions_activated_by", "knowledge_base_versions", type_="foreignkey")
    op.drop_constraint("fk_kb_versions_created_by", "knowledge_base_versions", type_="foreignkey")
    op.drop_constraint("fk_kb_versions_source", "knowledge_base_versions", type_="foreignkey")
    op.drop_column("knowledge_base_versions", "validation_report_json")
    op.drop_column("knowledge_base_versions", "validated_checksum")
    op.drop_column("knowledge_base_versions", "validated_at")
    op.drop_column("knowledge_base_versions", "activated_by")
    op.drop_column("knowledge_base_versions", "created_by")
    op.drop_column("knowledge_base_versions", "source_version_id")
