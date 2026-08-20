"""Add structured response feedback.

Revision ID: 0009_audit_feedback
Revises: 0008_review_before_send
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_audit_feedback"
down_revision: str | None = "0008_review_before_send"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rag_run_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category IN ('CORRECT', 'INCORRECT', 'MISSING_KB_INFORMATION', "
            "'CONFLICTING_KB_INFORMATION', 'RETRIEVAL_FAILURE', 'GROUNDING_FAILURE', "
            "'ESCALATION_APPROPRIATE', 'ESCALATION_UNNECESSARY')",
            name="ck_feedback_category",
        ),
        sa.ForeignKeyConstraint(["rag_run_id"], ["rag_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["admin_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_rag_run_created_at", "feedback", ["rag_run_id", "created_at"])
    op.create_index("ix_feedback_category_created_at", "feedback", ["category", "created_at"])
    op.execute(
        """
        CREATE FUNCTION prevent_feedback_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'feedback history is append-only';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER feedback_append_only
        BEFORE UPDATE OR DELETE ON feedback
        FOR EACH ROW EXECUTE FUNCTION prevent_feedback_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS feedback_append_only ON feedback")
    op.execute("DROP FUNCTION IF EXISTS prevent_feedback_mutation()")
    op.drop_index("ix_feedback_category_created_at", table_name="feedback")
    op.drop_index("ix_feedback_rag_run_created_at", table_name="feedback")
    op.drop_table("feedback")
