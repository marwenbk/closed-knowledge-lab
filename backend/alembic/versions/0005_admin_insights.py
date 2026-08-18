"""Persist structured RAG traces for the operator inspector.

Revision ID: 0005_admin_insights
Revises: 0004_human_takeover
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_admin_insights"
down_revision: str | None = "0004_human_takeover"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rag_runs",
        sa.Column(
            "conversation_context_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "rag_runs",
        sa.Column(
            "execution_trace_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("rag_runs", "execution_trace_json")
    op.drop_column("rag_runs", "conversation_context_json")
