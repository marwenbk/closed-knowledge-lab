from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class KnowledgeBaseVersion(Base):
    __tablename__ = "knowledge_base_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'RETIRED', 'FAILED')",
            name="ck_kb_versions_status",
        ),
        UniqueConstraint("dataset_id", "dataset_version", name="uq_kb_dataset_version"),
        Index(
            "uq_kb_one_active_dataset",
            "dataset_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_kb_versions_dataset_created_at", "dataset_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(50), nullable=False)
    generator_version: Mapped[str] = mapped_column(String(50), nullable=False)
    language: Mapped[str] = mapped_column(String(20), nullable=False)
    seed_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    template_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("knowledge_base_versions.id", ondelete="RESTRICT")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    activated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_checksum: Mapped[str | None] = mapped_column(String(64))
    validation_report_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("status IN ('IMPORTED', 'RETIRED')", name="ck_documents_status"),
        UniqueConstraint("kb_version_id", "document_key", name="uq_documents_version_key"),
        UniqueConstraint("kb_version_id", "source_path", name="uq_documents_version_path"),
        UniqueConstraint("id", "kb_version_id", name="uq_documents_id_version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    kb_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_base_versions.id", ondelete="RESTRICT"), nullable=False
    )
    document_key: Mapped[str] = mapped_column(String(150), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    language: Mapped[str] = mapped_column(String(20), nullable=False)
    source_path: Mapped[str] = mapped_column(String(500), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DocumentRevision(Base):
    __tablename__ = "document_revisions"
    __table_args__ = (
        CheckConstraint("revision_number > 0", name="ck_document_revisions_number"),
        UniqueConstraint("document_id", "revision_number", name="uq_document_revision_number"),
        UniqueConstraint("id", "document_id", name="uq_document_revisions_id_document"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    front_matter: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("ordinal > 0", name="ck_chunks_ordinal"),
        CheckConstraint("token_count > 0", name="ck_chunks_token_count"),
        CheckConstraint(
            "(embedding IS NULL AND embedding_model IS NULL AND embedding_version IS NULL "
            "AND embedding_dimensions IS NULL AND embedding_content_checksum IS NULL "
            "AND embedded_at IS NULL) OR "
            "(embedding IS NOT NULL AND embedding_model IS NOT NULL "
            "AND embedding_version IS NOT NULL AND embedding_dimensions = 384 "
            "AND embedding_content_checksum = content_checksum AND embedded_at IS NOT NULL)",
            name="ck_chunks_embedding_metadata_complete",
        ),
        ForeignKeyConstraint(
            ["document_id", "kb_version_id"],
            ["documents.id", "documents.kb_version_id"],
            name="fk_chunks_document_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["revision_id", "document_id"],
            ["document_revisions.id", "document_revisions.document_id"],
            name="fk_chunks_revision_document",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("kb_version_id", "stable_chunk_key", name="uq_chunks_stable_key"),
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_chunks_content_trgm",
            "content_normalized",
            postgresql_using="gin",
            postgresql_ops={"content_normalized": "gin_trgm_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    kb_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_base_versions.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False
    )
    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    stable_chunk_key: Mapped[str] = mapped_column(String(250), nullable=False)
    section: Mapped[str] = mapped_column(String(500), nullable=False)
    section_path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    content_checksum: Mapped[str] = mapped_column(
        String(64),
        Computed("encode(sha256(content_normalized::bytea), 'hex')", persisted=True),
    )
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[Any | None] = mapped_column(VECTOR(384))
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    embedding_version: Mapped[str | None] = mapped_column(String(100))
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer)
    embedding_content_checksum: Mapped[str | None] = mapped_column(String(64))
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('portuguese'::regconfig, content_normalized)",
            persisted=True,
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_created_at", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(200))
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(200), nullable=False)
    request_id: Mapped[UUID | None] = mapped_column(Uuid)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WidgetSession(Base):
    __tablename__ = "widget_sessions"
    __table_args__ = (
        Index("ix_widget_sessions_expires_at", "expires_at"),
        Index("ix_widget_sessions_origin_created_at", "origin", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    anonymous_subject: Mapped[str] = mapped_column(String(100), nullable=False)
    origin: Mapped[str] = mapped_column(String(300), nullable=False)
    locale: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AdminUser(Base):
    __tablename__ = "admin_users"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="ck_admin_users_status"),
        UniqueConstraint("email", name="uq_admin_users_email"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AdminUserRole(Base):
    __tablename__ = "admin_user_roles"
    __table_args__ = (
        CheckConstraint(
            "role IN ('ADMIN', 'SUPERVISOR', 'SUPPORT_AGENT', 'HUMAN_REVIEWER', "
            "'KNOWLEDGE_EDITOR', 'AUDITOR')",
            name="ck_admin_user_roles_role",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(30), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_admin_sessions_token_hash"),
        Index("ix_admin_sessions_user_expires_at", "user_id", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'EVALUATED', 'ACTIVE', 'RETIRED')",
            name="ck_prompt_versions_status",
        ),
        UniqueConstraint("version", name="uq_prompt_versions_version"),
        Index(
            "uq_prompt_versions_one_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_prompt_versions_created_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    answerability_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    generation_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    verification_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    source_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="RESTRICT")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    activated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SettingsVersion(Base):
    __tablename__ = "settings_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'EVALUATED', 'ACTIVE', 'RETIRED')",
            name="ck_settings_versions_status",
        ),
        UniqueConstraint("version", name="uq_settings_versions_version"),
        Index(
            "uq_settings_versions_one_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_settings_versions_created_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    requires_reindex: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("settings_versions.id", ondelete="RESTRICT")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    activated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING', 'PASSED', 'FAILED')",
            name="ck_evaluation_runs_status",
        ),
        Index("ix_evaluation_runs_version_started_at", "kb_version_id", "started_at"),
        Index(
            "uq_evaluation_runs_one_running_version",
            "kb_version_id",
            unique=True,
            postgresql_where=text("status = 'RUNNING'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    suite: Mapped[str] = mapped_column(String(100), nullable=False)
    kb_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_base_versions.id", ondelete="RESTRICT"), nullable=False
    )
    kb_manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(
        String(100), ForeignKey("prompt_versions.version", ondelete="RESTRICT"), nullable=False
    )
    settings_version: Mapped[str] = mapped_column(
        String(100), ForeignKey("settings_versions.version", ondelete="RESTRICT"), nullable=False
    )
    prompt_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    settings_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    baseline_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="RESTRICT")
    )
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(100), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    started_by: Mapped[UUID] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('AI_ACTIVE', 'AI_REVIEW_PENDING', 'HUMAN_REQUESTED', "
            "'HUMAN_ASSIGNED', 'HUMAN_ACTIVE', 'RETURNED_TO_AI', 'CLOSED')",
            name="ck_conversations_state",
        ),
        CheckConstraint(
            "priority IS NULL OR priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')",
            name="ck_conversations_priority",
        ),
        CheckConstraint(
            "state NOT IN ('HUMAN_REQUESTED', 'HUMAN_ASSIGNED', 'HUMAN_ACTIVE') OR "
            "(priority IS NOT NULL AND handoff_reason IS NOT NULL "
            "AND handoff_requested_at IS NOT NULL)",
            name="ck_conversations_handoff_required",
        ),
        CheckConstraint(
            "state NOT IN ('HUMAN_ASSIGNED', 'HUMAN_ACTIVE') OR "
            "(assigned_agent_id IS NOT NULL AND claimed_at IS NOT NULL)",
            name="ck_conversations_assignment_required",
        ),
        CheckConstraint(
            "state <> 'HUMAN_REQUESTED' OR (assigned_agent_id IS NULL AND claimed_at IS NULL)",
            name="ck_conversations_request_unassigned",
        ),
        Index("ix_conversations_session_created_at", "widget_session_id", "created_at"),
        Index("ix_conversations_state_last_message_at", "state", "last_message_at"),
        Index(
            "ix_conversations_handoff_queue",
            "state",
            "priority",
            "handoff_requested_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    widget_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("widget_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    priority: Mapped[str | None] = mapped_column(String(20))
    handoff_reason: Mapped[str | None] = mapped_column(String(50))
    handoff_trigger_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "messages.id",
            name="fk_conversations_handoff_trigger_message",
            ondelete="RESTRICT",
            use_alter=True,
        )
    )
    handoff_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "sender_type IN ('CUSTOMER', 'AI', 'HUMAN', 'SYSTEM', 'INTERNAL')",
            name="ck_messages_sender_type",
        ),
        CheckConstraint("visibility IN ('PUBLIC', 'INTERNAL')", name="ck_messages_visibility"),
        CheckConstraint(
            "status IN ('PENDING', 'PERSISTED', 'DELIVERED', 'FAILED')",
            name="ck_messages_status",
        ),
        CheckConstraint(
            "(sender_type IN ('CUSTOMER', 'HUMAN', 'INTERNAL') "
            "AND client_message_id IS NOT NULL) OR "
            "(sender_type IN ('AI', 'SYSTEM') AND client_message_id IS NULL)",
            name="ck_messages_client_id_owner",
        ),
        CheckConstraint(
            "(sender_type = 'CUSTOMER' AND visibility = 'PUBLIC' "
            "AND sender_user_id IS NULL) OR "
            "(sender_type = 'AI' AND visibility = 'PUBLIC' AND sender_user_id IS NULL) OR "
            "(sender_type = 'HUMAN' AND visibility = 'PUBLIC' "
            "AND sender_user_id IS NOT NULL) OR "
            "(sender_type = 'INTERNAL' AND visibility = 'INTERNAL' "
            "AND sender_user_id IS NOT NULL) OR "
            "(sender_type = 'SYSTEM' AND sender_user_id IS NULL)",
            name="ck_messages_sender_visibility",
        ),
        UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_messages_conversation_client_id",
        ),
        Index("ix_messages_conversation_created_at", "conversation_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False
    )
    client_message_id: Mapped[UUID | None] = mapped_column(Uuid)
    sender_type: Mapped[str] = mapped_column(String(20), nullable=False)
    sender_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    reply_to_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT")
    )
    citations_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RagRun(Base):
    __tablename__ = "rag_runs"
    __table_args__ = (
        CheckConstraint("status IN ('RUNNING', 'COMPLETED', 'FAILED')", name="ck_rag_runs_status"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_rag_runs_latency"),
        UniqueConstraint("user_message_id", name="uq_rag_runs_user_message"),
        UniqueConstraint("assistant_message_id", name="uq_rag_runs_assistant_message"),
        Index("ix_rag_runs_conversation_created_at", "conversation_id", "created_at"),
        Index(
            "uq_rag_runs_one_running_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'RUNNING'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False
    )
    user_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False
    )
    assistant_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT")
    )
    kb_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_base_versions.id", ondelete="RESTRICT"), nullable=False
    )
    original_query: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_query: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_context_json: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    execution_trace_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    answerability_status: Mapped[str | None] = mapped_column(String(30))
    verification_status: Mapped[str | None] = mapped_column(String(30))
    model_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(
        String(100), ForeignKey("prompt_versions.version", ondelete="RESTRICT"), nullable=False
    )
    embedding_version: Mapped[str] = mapped_column(String(100), nullable=False)
    settings_version: Mapped[str] = mapped_column(
        String(100), ForeignKey("settings_versions.version", ondelete="RESTRICT"), nullable=False
    )
    regenerated: Mapped[bool | None] = mapped_column()
    latency_ms: Mapped[float | None] = mapped_column()
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationEvent(Base):
    __tablename__ = "conversation_events"
    __table_args__ = (
        CheckConstraint(
            "visibility IN ('PUBLIC', 'INTERNAL')",
            name="ck_conversation_events_visibility",
        ),
        Index("ix_conversation_events_conversation_id_id", "conversation_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HandoffEvent(Base):
    __tablename__ = "handoff_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('REQUEST', 'CLAIM', 'START', 'MESSAGE', 'NOTE', 'RETURN', 'CLOSE')",
            name="ck_handoff_events_type",
        ),
        Index("ix_handoff_events_conversation_id", "conversation_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(100))
    from_state: Mapped[str] = mapped_column(String(30), nullable=False)
    to_state: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(200))
    trigger_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT")
    )
    request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
