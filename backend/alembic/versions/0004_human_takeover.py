"""Add authenticated human takeover.

Revision ID: 0004_human_takeover
Revises: 0003_conversation_backbone
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_human_takeover"
down_revision: str | None = "0003_conversation_backbone"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
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
        sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="ck_admin_users_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_admin_users_email"),
    )
    op.create_table(
        "admin_user_roles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('ADMIN', 'SUPERVISOR', 'SUPPORT_AGENT', 'HUMAN_REVIEWER', "
            "'KNOWLEDGE_EDITOR', 'AUDITOR')",
            name="ck_admin_user_roles_role",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["admin_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id", "role"),
    )
    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["admin_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_admin_sessions_token_hash"),
    )
    op.create_index(
        "ix_admin_sessions_user_expires_at",
        "admin_sessions",
        ["user_id", "expires_at"],
    )

    op.add_column("conversations", sa.Column("priority", sa.String(length=20)))
    op.add_column("conversations", sa.Column("handoff_reason", sa.String(length=50)))
    op.add_column("conversations", sa.Column("handoff_trigger_message_id", sa.Uuid()))
    op.add_column("conversations", sa.Column("handoff_requested_at", sa.DateTime(timezone=True)))
    op.add_column("conversations", sa.Column("assigned_agent_id", sa.Uuid()))
    op.add_column("conversations", sa.Column("claimed_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_conversations_priority",
        "conversations",
        "priority IS NULL OR priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')",
    )
    op.create_check_constraint(
        "ck_conversations_handoff_required",
        "conversations",
        "state NOT IN ('HUMAN_REQUESTED', 'HUMAN_ASSIGNED', 'HUMAN_ACTIVE') OR "
        "(priority IS NOT NULL AND handoff_reason IS NOT NULL "
        "AND handoff_requested_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_conversations_assignment_required",
        "conversations",
        "state NOT IN ('HUMAN_ASSIGNED', 'HUMAN_ACTIVE') OR "
        "(assigned_agent_id IS NOT NULL AND claimed_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_conversations_request_unassigned",
        "conversations",
        "state <> 'HUMAN_REQUESTED' OR (assigned_agent_id IS NULL AND claimed_at IS NULL)",
    )
    op.create_foreign_key(
        "fk_conversations_handoff_trigger_message",
        "conversations",
        "messages",
        ["handoff_trigger_message_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_conversations_assigned_agent",
        "conversations",
        "admin_users",
        ["assigned_agent_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_conversations_handoff_queue",
        "conversations",
        ["state", "priority", "handoff_requested_at"],
    )

    op.add_column("messages", sa.Column("sender_user_id", sa.Uuid()))
    op.drop_constraint("ck_messages_client_id_owner", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_client_id_owner",
        "messages",
        "(sender_type IN ('CUSTOMER', 'HUMAN', 'INTERNAL') "
        "AND client_message_id IS NOT NULL) OR "
        "(sender_type IN ('AI', 'SYSTEM') AND client_message_id IS NULL)",
    )
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
    op.create_foreign_key(
        "fk_messages_sender_user",
        "messages",
        "admin_users",
        ["sender_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "handoff_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=100)),
        sa.Column("from_state", sa.String(length=30), nullable=False),
        sa.Column("to_state", sa.String(length=30), nullable=False),
        sa.Column("actor_type", sa.String(length=50), nullable=False),
        sa.Column("actor_id", sa.String(length=200)),
        sa.Column("trigger_message_id", sa.Uuid()),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('REQUEST', 'CLAIM', 'START', 'MESSAGE', 'NOTE', 'RETURN', 'CLOSE')",
            name="ck_handoff_events_type",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["trigger_message_id"], ["messages.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_handoff_events_conversation_id",
        "handoff_events",
        ["conversation_id", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION prevent_handoff_event_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'handoff_events are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER handoff_events_append_only
        BEFORE UPDATE OR DELETE ON handoff_events
        FOR EACH ROW EXECUTE FUNCTION prevent_handoff_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS handoff_events_append_only ON handoff_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_handoff_event_mutation()")
    op.drop_index("ix_handoff_events_conversation_id", table_name="handoff_events")
    op.drop_table("handoff_events")

    op.drop_constraint("fk_messages_sender_user", "messages", type_="foreignkey")
    op.drop_constraint("ck_messages_sender_visibility", "messages", type_="check")
    op.drop_constraint("ck_messages_client_id_owner", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_client_id_owner",
        "messages",
        "(sender_type = 'CUSTOMER' AND client_message_id IS NOT NULL) OR "
        "(sender_type <> 'CUSTOMER' AND client_message_id IS NULL)",
    )
    op.drop_column("messages", "sender_user_id")

    op.drop_index("ix_conversations_handoff_queue", table_name="conversations")
    op.drop_constraint("fk_conversations_assigned_agent", "conversations", type_="foreignkey")
    op.drop_constraint(
        "fk_conversations_handoff_trigger_message", "conversations", type_="foreignkey"
    )
    op.drop_constraint("ck_conversations_request_unassigned", "conversations", type_="check")
    op.drop_constraint("ck_conversations_assignment_required", "conversations", type_="check")
    op.drop_constraint("ck_conversations_handoff_required", "conversations", type_="check")
    op.drop_constraint("ck_conversations_priority", "conversations", type_="check")
    op.drop_column("conversations", "claimed_at")
    op.drop_column("conversations", "assigned_agent_id")
    op.drop_column("conversations", "handoff_requested_at")
    op.drop_column("conversations", "handoff_trigger_message_id")
    op.drop_column("conversations", "handoff_reason")
    op.drop_column("conversations", "priority")

    op.drop_index("ix_admin_sessions_user_expires_at", table_name="admin_sessions")
    op.drop_table("admin_sessions")
    op.drop_table("admin_user_roles")
    op.drop_table("admin_users")
