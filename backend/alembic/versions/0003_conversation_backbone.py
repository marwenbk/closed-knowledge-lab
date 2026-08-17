"""Add the persisted conversation and realtime backbone.

Revision ID: 0003_conversation_backbone
Revises: 0002_embedding_retrieval
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_conversation_backbone"
down_revision: str | None = "0002_embedding_retrieval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CONVERSATION_STATES = (
    "'AI_ACTIVE', 'AI_REVIEW_PENDING', 'HUMAN_REQUESTED', 'HUMAN_ASSIGNED', "
    "'HUMAN_ACTIVE', 'RETURNED_TO_AI', 'CLOSED'"
)


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("actor_id", sa.String(length=200)))

    op.create_table(
        "widget_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("anonymous_subject", sa.String(length=100), nullable=False),
        sa.Column("origin", sa.String(length=300), nullable=False),
        sa.Column("locale", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_widget_sessions_expires_at", "widget_sessions", ["expires_at"])
    op.create_index(
        "ix_widget_sessions_origin_created_at",
        "widget_sessions",
        ["origin", "created_at"],
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("widget_session_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(f"state IN ({CONVERSATION_STATES})", name="ck_conversations_state"),
        sa.ForeignKeyConstraint(["widget_session_id"], ["widget_sessions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversations_session_created_at",
        "conversations",
        ["widget_session_id", "created_at"],
    )
    op.create_index(
        "ix_conversations_state_last_message_at",
        "conversations",
        ["state", "last_message_at"],
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("client_message_id", sa.Uuid()),
        sa.Column("sender_type", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reply_to_message_id", sa.Uuid()),
        sa.Column(
            "citations_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sender_type IN ('CUSTOMER', 'AI', 'HUMAN', 'SYSTEM', 'INTERNAL')",
            name="ck_messages_sender_type",
        ),
        sa.CheckConstraint("visibility IN ('PUBLIC', 'INTERNAL')", name="ck_messages_visibility"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PERSISTED', 'DELIVERED', 'FAILED')",
            name="ck_messages_status",
        ),
        sa.CheckConstraint(
            "(sender_type = 'CUSTOMER' AND client_message_id IS NOT NULL) OR "
            "(sender_type <> 'CUSTOMER' AND client_message_id IS NULL)",
            name="ck_messages_client_id_owner",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reply_to_message_id"], ["messages.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_messages_conversation_client_id",
        ),
    )
    op.create_index(
        "ix_messages_conversation_created_at",
        "messages",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "rag_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_message_id", sa.Uuid(), nullable=False),
        sa.Column("assistant_message_id", sa.Uuid()),
        sa.Column("kb_version_id", sa.Uuid(), nullable=False),
        sa.Column("original_query", sa.Text(), nullable=False),
        sa.Column("retrieval_query", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("answerability_status", sa.String(length=30)),
        sa.Column("verification_status", sa.String(length=30)),
        sa.Column("model_provider", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("model_version", sa.String(length=200)),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("embedding_version", sa.String(length=100), nullable=False),
        sa.Column("settings_version", sa.String(length=100), nullable=False),
        sa.Column("regenerated", sa.Boolean()),
        sa.Column("latency_ms", sa.Float()),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'FAILED')", name="ck_rag_runs_status"
        ),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_rag_runs_latency"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_message_id"], ["messages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["messages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["kb_version_id"], ["knowledge_base_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_message_id", name="uq_rag_runs_user_message"),
        sa.UniqueConstraint("assistant_message_id", name="uq_rag_runs_assistant_message"),
    )
    op.create_index(
        "ix_rag_runs_conversation_created_at",
        "rag_runs",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "uq_rag_runs_one_running_conversation",
        "rag_runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )

    op.create_table(
        "conversation_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("actor_type", sa.String(length=50), nullable=False),
        sa.Column("actor_id", sa.String(length=200)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "visibility IN ('PUBLIC', 'INTERNAL')",
            name="ck_conversation_events_visibility",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_events_conversation_id_id",
        "conversation_events",
        ["conversation_id", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION prevent_conversation_event_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'conversation_events are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER conversation_events_append_only
        BEFORE UPDATE OR DELETE ON conversation_events
        FOR EACH ROW EXECUTE FUNCTION prevent_conversation_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS conversation_events_append_only ON conversation_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_conversation_event_mutation()")
    op.drop_index("ix_conversation_events_conversation_id_id", table_name="conversation_events")
    op.drop_table("conversation_events")
    op.drop_index(
        "uq_rag_runs_one_running_conversation",
        table_name="rag_runs",
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
    op.drop_index("ix_rag_runs_conversation_created_at", table_name="rag_runs")
    op.drop_table("rag_runs")
    op.drop_index("ix_messages_conversation_created_at", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_state_last_message_at", table_name="conversations")
    op.drop_index("ix_conversations_session_created_at", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_widget_sessions_origin_created_at", table_name="widget_sessions")
    op.drop_index("ix_widget_sessions_expires_at", table_name="widget_sessions")
    op.drop_table("widget_sessions")
    op.drop_column("audit_events", "actor_id")
