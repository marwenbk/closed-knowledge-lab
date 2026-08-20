"""Add review-before-send workflow.

Revision ID: 0008_review_before_send
Revises: 0007_runtime_tuning
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_review_before_send"
down_revision: str | None = "0007_runtime_tuning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("review_status", sa.String(length=20), server_default="NONE", nullable=False),
    )
    op.add_column(
        "messages",
        sa.Column("review_regeneration_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_messages_review_status",
        "messages",
        "review_status IN ('NONE', 'PENDING', 'REGENERATING', 'APPROVED', 'EDITED', 'REJECTED')",
    )
    op.create_check_constraint(
        "ck_messages_review_regeneration_count",
        "messages",
        "review_regeneration_count BETWEEN 0 AND 1",
    )
    op.create_check_constraint(
        "ck_messages_review_owner",
        "messages",
        "(sender_type = 'AI') OR (review_status = 'NONE' AND review_regeneration_count = 0)",
    )
    op.drop_constraint("ck_messages_sender_visibility", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_sender_visibility",
        "messages",
        "(sender_type = 'CUSTOMER' AND visibility = 'PUBLIC' AND sender_user_id IS NULL) OR "
        "(sender_type = 'AI' AND visibility IN ('PUBLIC', 'INTERNAL') "
        "AND sender_user_id IS NULL) OR "
        "(sender_type = 'HUMAN' AND visibility = 'PUBLIC' AND sender_user_id IS NOT NULL) OR "
        "(sender_type = 'INTERNAL' AND visibility = 'INTERNAL' "
        "AND sender_user_id IS NOT NULL) OR "
        "(sender_type = 'SYSTEM' AND sender_user_id IS NULL)",
    )
    op.create_index(
        "ix_messages_pending_review",
        "messages",
        ["created_at"],
        postgresql_where=sa.text("review_status IN ('PENDING', 'REGENERATING')"),
    )
    op.create_index(
        "ix_conversations_review_pending",
        "conversations",
        ["updated_at"],
        postgresql_where=sa.text("state = 'AI_REVIEW_PENDING'"),
    )

    op.create_table(
        "message_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("original_content", sa.Text(), nullable=False),
        sa.Column("final_content", sa.Text()),
        sa.Column(
            "diff_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("note", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('APPROVE', 'EDIT_AND_SEND', 'REJECT_AND_REGENERATE', "
            "'REJECT_AND_TAKEOVER', 'CLOSE')",
            name="ck_message_reviews_action",
        ),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["admin_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_message_reviews_message_created_at",
        "message_reviews",
        ["message_id", "created_at"],
    )
    op.execute(
        """
        CREATE FUNCTION prevent_message_review_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'message review history is append-only';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER message_reviews_append_only
        BEFORE UPDATE OR DELETE ON message_reviews
        FOR EACH ROW EXECUTE FUNCTION prevent_message_review_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS message_reviews_append_only ON message_reviews")
    op.execute("DROP FUNCTION IF EXISTS prevent_message_review_mutation()")
    op.drop_index("ix_message_reviews_message_created_at", table_name="message_reviews")
    op.drop_table("message_reviews")
    op.drop_index("ix_conversations_review_pending", table_name="conversations")
    op.drop_index("ix_messages_pending_review", table_name="messages")
    op.drop_constraint("ck_messages_sender_visibility", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_sender_visibility",
        "messages",
        "(sender_type = 'CUSTOMER' AND visibility = 'PUBLIC' AND sender_user_id IS NULL) OR "
        "(sender_type = 'AI' AND visibility = 'PUBLIC' AND sender_user_id IS NULL) OR "
        "(sender_type = 'HUMAN' AND visibility = 'PUBLIC' AND sender_user_id IS NOT NULL) OR "
        "(sender_type = 'INTERNAL' AND visibility = 'INTERNAL' "
        "AND sender_user_id IS NOT NULL) OR "
        "(sender_type = 'SYSTEM' AND sender_user_id IS NULL)",
    )
    op.drop_constraint("ck_messages_review_owner", "messages", type_="check")
    op.drop_constraint("ck_messages_review_regeneration_count", "messages", type_="check")
    op.drop_constraint("ck_messages_review_status", "messages", type_="check")
    op.drop_column("messages", "review_regeneration_count")
    op.drop_column("messages", "review_status")
