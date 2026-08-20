from __future__ import annotations

import difflib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.admin_auth import AdminPrincipal
from app.answering import VerificationDecision, answer_knowledge_with_trace
from app.config import Settings
from app.conversations import ConversationError, record_audit_event, record_conversation_event
from app.embeddings import EmbeddingProvider
from app.llm import LLMProvider
from app.models import (
    Conversation,
    Message,
    MessageReview,
    PromptVersion,
    RagRun,
    SettingsVersion,
)
from app.retrieval import RetrievalMatch, RetrievalResult
from app.tuning import PromptBundle, RetrievalTuning

ReviewAction = Literal[
    "APPROVE",
    "EDIT_AND_SEND",
    "REJECT_AND_REGENERATE",
    "REJECT_AND_TAKEOVER",
    "CLOSE",
]


def _now() -> datetime:
    return datetime.now(UTC)


def _diff(before: str, after: str | None) -> dict[str, Any]:
    if after is None:
        return {"changed": False, "operations": []}
    operations = [
        {"operation": tag, "before": before[i1:i2], "after": after[j1:j2]}
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after).get_opcodes()
        if tag != "equal"
    ]
    return {"changed": before != after, "operations": operations}


def _pending(
    session: Session, message_id: UUID, *, lock: bool = False
) -> tuple[Message, Conversation, RagRun]:
    statement = select(Message).where(Message.id == message_id)
    message = session.scalar(statement.with_for_update() if lock else statement)
    if message is None:
        raise ConversationError(404, "REVIEW_MESSAGE_NOT_FOUND", "Review message not found")
    conversation = session.get(Conversation, message.conversation_id, with_for_update=lock)
    run = session.scalar(select(RagRun).where(RagRun.assistant_message_id == message.id))
    if conversation is None or run is None:
        raise ConversationError(409, "REVIEW_STATE_INVALID", "Review state is incomplete")
    if conversation.state != "AI_REVIEW_PENDING" or message.review_status not in {
        "PENDING",
        "REGENERATING",
    }:
        raise ConversationError(409, "REVIEW_NOT_PENDING", "The message is not pending review")
    return message, conversation, run


def _review_record(
    session: Session,
    message: Message,
    principal: AdminPrincipal,
    action: ReviewAction,
    *,
    original: str,
    final: str | None,
    note: str | None,
) -> None:
    session.add(
        MessageReview(
            id=uuid4(),
            message_id=message.id,
            reviewer_id=principal.user_id,
            action=action,
            original_content=original,
            final_content=final,
            diff_json=_diff(original, final),
            note=note,
        )
    )


def _verification_messages(
    prompt: str, content: str, citations: list[dict[str, Any]]
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": prompt
            + " Avalie o texto final editado e use somente os trechos de citação fornecidos.",
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "edited_answer": content,
                    "evidence": [
                        {
                            "document": citation.get("document"),
                            "section": citation.get("section"),
                            "quote": citation.get("quote"),
                        }
                        for citation in citations
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]


def _verify_edit(
    engine: Engine,
    provider: LLMProvider,
    message_id: UUID,
    content: str,
) -> str:
    clean = content.strip()
    if not clean or len(clean) > 4000:
        raise ConversationError(422, "REVIEW_CONTENT_INVALID", "Edited content is invalid")
    with Session(engine) as session:
        message, _conversation, run = _pending(session, message_id)
        prompt = session.scalar(
            select(PromptVersion).where(PromptVersion.version == run.prompt_version)
        )
        if prompt is None:
            raise ConversationError(
                409, "REVIEW_PROVENANCE_MISSING", "Prompt provenance is missing"
            )
        citations = list(message.citations_json)
    decision = provider.structured_generate(
        _verification_messages(prompt.verification_prompt, clean, citations),
        VerificationDecision,
    )
    if not decision.supported or decision.issues:
        raise ConversationError(
            409,
            "REVIEW_EDIT_NOT_GROUNDED",
            "The edited response is not fully supported by its stored citations",
        )
    return clean


def _deliver(
    engine: Engine,
    principal: AdminPrincipal,
    message_id: UUID,
    *,
    action: Literal["APPROVE", "EDIT_AND_SEND"],
    final_content: str | None,
    note: str | None,
    request_id: UUID,
) -> None:
    now = _now()
    with Session(engine) as session, session.begin():
        message, conversation, run = _pending(session, message_id, lock=True)
        if message.review_status != "PENDING":
            raise ConversationError(409, "REVIEW_NOT_PENDING", "The message is not pending review")
        original = message.content
        if final_content is not None:
            message.content = final_content
        message.visibility = "PUBLIC"
        message.status = "DELIVERED"
        message.review_status = "APPROVED" if action == "APPROVE" else "EDITED"
        message.delivered_at = now
        conversation.state = "AI_ACTIVE"
        conversation.last_message_at = now
        conversation.updated_at = now
        _review_record(
            session,
            message,
            principal,
            action,
            original=original,
            final=message.content,
            note=note,
        )
        payload = {
            "message_id": str(message.id),
            "rag_run_id": str(run.id),
            "sender": {"type": "AI", "label": "TopMed Guide"},
            "status": run.answerability_status,
            "content": message.content,
            "citations": message.citations_json,
            "created_at": message.created_at.isoformat(),
        }
        record_conversation_event(
            session,
            conversation.id,
            "message.created",
            payload,
            actor_type="AI",
            actor_id=run.model_name,
        )
        record_conversation_event(
            session,
            conversation.id,
            "message.delivered",
            {"message_id": str(message.id), "delivered_at": now.isoformat()},
            actor_type="ADMIN",
            actor_id=str(principal.user_id),
        )
        record_conversation_event(
            session,
            conversation.id,
            "review.completed",
            {"message_id": str(message.id), "action": action, "to_state": "AI_ACTIVE"},
            actor_type="ADMIN",
            actor_id=str(principal.user_id),
            visibility="INTERNAL",
        )
        record_audit_event(
            session,
            "message.reviewed",
            "message",
            message.id,
            actor_type="ADMIN",
            actor_id=str(principal.user_id),
            request_id=request_id,
            before={"review_status": "PENDING", "conversation_state": "AI_REVIEW_PENDING"},
            after={"review_status": message.review_status, "conversation_state": "AI_ACTIVE"},
            metadata={"action": action, "content_changed": original != message.content},
        )


def _retrieval(trace: dict[str, Any]) -> RetrievalResult:
    payload = trace.get("retrieval")
    if not isinstance(payload, dict):
        raise ConversationError(409, "REVIEW_TRACE_MISSING", "Retrieval trace is missing")
    matches = tuple(
        RetrievalMatch(
            chunk_id=UUID(str(item["chunk_id"])),
            stable_chunk_key=str(item["stable_chunk_key"]),
            document_key=str(item["document_key"]),
            document_title=str(item["document_title"]),
            source_path=str(item["source_path"]),
            section=str(item["section"]),
            section_path=tuple(str(value) for value in item["section_path"]),
            ordinal=int(item["ordinal"]),
            content=str(item["content"]),
            rrf_score=float(item["rrf_score"]),
            signals=dict(item.get("signals", {})),
        )
        for item in payload.get("matches", [])
    )
    second_hop = payload.get("second_hop", {})
    return RetrievalResult(
        query=str(payload["query"]),
        dataset_id=str(payload["dataset_id"]),
        dataset_version=str(payload["dataset_version"]),
        embedding_model=str(payload["embedding_model"]),
        embedding_version=str(payload["embedding_version"]),
        matches=matches,
        trigram_fallback_used=bool(payload.get("trigram_fallback_used")),
        second_hop_query=(
            str(second_hop.get("query")) if second_hop.get("query") is not None else None
        ),
        duration_ms=float(payload.get("duration_ms", 0)),
    )


def regenerate_review(
    engine: Engine,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider,
    base: Settings,
    principal: AdminPrincipal,
    message_id: UUID,
    *,
    note: str | None,
    request_id: UUID,
) -> None:
    with Session(engine) as session, session.begin():
        message, _conversation, run = _pending(session, message_id, lock=True)
        if message.review_status != "PENDING" or message.review_regeneration_count >= 1:
            raise ConversationError(
                409, "REVIEW_REGENERATION_LIMIT", "The proposal can be regenerated only once"
            )
        original = message.content
        message.review_status = "REGENERATING"
        run_id = run.id

    try:
        with Session(engine) as session:
            current_run = session.get(RagRun, run_id)
            if current_run is None or current_run.execution_trace_json is None:
                raise ConversationError(409, "REVIEW_TRACE_MISSING", "RAG trace is missing")
            prompt = session.scalar(
                select(PromptVersion).where(PromptVersion.version == current_run.prompt_version)
            )
            settings_version = session.scalar(
                select(SettingsVersion).where(
                    SettingsVersion.version == current_run.settings_version
                )
            )
            if prompt is None or settings_version is None:
                raise ConversationError(
                    409, "REVIEW_PROVENANCE_MISSING", "Runtime provenance is missing"
                )
            prompts = PromptBundle(
                answerability_prompt=prompt.answerability_prompt,
                generation_prompt=prompt.generation_prompt,
                verification_prompt=prompt.verification_prompt,
            )
            retrieval_settings = RetrievalTuning.model_validate(settings_version.settings_json)
            effective = base.model_copy(
                update={
                    **retrieval_settings.model_dump(),
                    "prompt_version": prompt.version,
                    "settings_version": settings_version.version,
                }
            )
            trace = dict(current_run.execution_trace_json)
            query = current_run.original_query
            context = tuple(current_run.conversation_context_json)
        execution = answer_knowledge_with_trace(
            engine,
            embedding_provider,
            llm_provider,
            effective,
            prompts,
            query,
            conversation_context=context,
            retrieval_result=_retrieval(trace),
        )
    except Exception:
        with Session(engine) as session, session.begin():
            resetting_message = session.get(Message, message_id, with_for_update=True)
            if resetting_message is not None and resetting_message.review_status == "REGENERATING":
                resetting_message.review_status = "PENDING"
        raise

    with Session(engine) as session, session.begin():
        message, conversation, run = _pending(session, message_id, lock=True)
        if message.review_status != "REGENERATING" or message.review_regeneration_count != 0:
            raise ConversationError(409, "REVIEW_STATE_CHANGED", "Review state changed")
        citations = [value.model_dump(mode="json") for value in execution.answer.citations]
        message.content = execution.answer.answer
        message.citations_json = citations
        message.review_status = "PENDING"
        message.review_regeneration_count = 1
        run.answerability_status = execution.answer.status
        run.verification_status = execution.answer.verification_status
        run.execution_trace_json = execution.trace
        run.regenerated = True
        run.latency_ms = execution.answer.duration_ms
        _review_record(
            session,
            message,
            principal,
            "REJECT_AND_REGENERATE",
            original=original,
            final=message.content,
            note=note,
        )
        record_conversation_event(
            session,
            conversation.id,
            "review.regenerated",
            {"message_id": str(message.id), "regeneration_count": 1},
            actor_type="ADMIN",
            actor_id=str(principal.user_id),
            visibility="INTERNAL",
        )
        record_audit_event(
            session,
            "message.review_regenerated",
            "message",
            message.id,
            actor_type="ADMIN",
            actor_id=str(principal.user_id),
            request_id=request_id,
            before={"review_status": "PENDING", "regeneration_count": 0},
            after={"review_status": "PENDING", "regeneration_count": 1},
        )


def reject_review(
    engine: Engine,
    principal: AdminPrincipal,
    message_id: UUID,
    *,
    action: Literal["REJECT_AND_TAKEOVER", "CLOSE"],
    note: str | None,
    request_id: UUID,
) -> None:
    from app.handoffs import request_handoff_in_session

    with Session(engine) as session, session.begin():
        message, conversation, _run = _pending(session, message_id, lock=True)
        if message.review_status != "PENDING":
            raise ConversationError(409, "REVIEW_NOT_PENDING", "The message is not pending review")
        message.review_status = "REJECTED"
        message.status = "FAILED"
        _review_record(
            session,
            message,
            principal,
            action,
            original=message.content,
            final=None,
            note=note,
        )
        if action == "REJECT_AND_TAKEOVER":
            request_handoff_in_session(
                session,
                conversation,
                reason="REVIEW_REJECTED",
                priority="HIGH",
                trigger_message_id=message.reply_to_message_id,
                actor_type="ADMIN",
                actor_id=str(principal.user_id),
                request_id=request_id,
            )
        else:
            previous = conversation.state
            conversation.state = "CLOSED"
            conversation.closed_at = _now()
            conversation.updated_at = conversation.closed_at
            record_conversation_event(
                session,
                conversation.id,
                "conversation.closed",
                {"from_state": previous, "to_state": "CLOSED"},
                actor_type="ADMIN",
                actor_id=str(principal.user_id),
            )
        record_audit_event(
            session,
            "message.review_rejected",
            "message",
            message.id,
            actor_type="ADMIN",
            actor_id=str(principal.user_id),
            request_id=request_id,
            before={"review_status": "PENDING", "conversation_state": "AI_REVIEW_PENDING"},
            after={"review_status": "REJECTED", "conversation_state": conversation.state},
            metadata={"action": action, "note": note},
        )


def review_message(
    engine: Engine,
    embedding_provider: EmbeddingProvider | None,
    llm_provider: LLMProvider | None,
    base: Settings,
    principal: AdminPrincipal,
    message_id: UUID,
    *,
    action: ReviewAction,
    content: str | None,
    note: str | None,
    request_id: UUID,
) -> UUID:
    if action == "APPROVE":
        _deliver(
            engine,
            principal,
            message_id,
            action="APPROVE",
            final_content=None,
            note=note,
            request_id=request_id,
        )
    elif action == "EDIT_AND_SEND":
        if content is None or llm_provider is None:
            raise ConversationError(422, "REVIEW_CONTENT_REQUIRED", "Edited content is required")
        verified = _verify_edit(engine, llm_provider, message_id, content)
        _deliver(
            engine,
            principal,
            message_id,
            action="EDIT_AND_SEND",
            final_content=verified,
            note=note,
            request_id=request_id,
        )
    elif action == "REJECT_AND_REGENERATE":
        if embedding_provider is None or llm_provider is None:
            raise ConversationError(
                503, "REVIEW_RUNTIME_NOT_READY", "Review runtime is unavailable"
            )
        regenerate_review(
            engine,
            embedding_provider,
            llm_provider,
            base,
            principal,
            message_id,
            note=note,
            request_id=request_id,
        )
    else:
        reject_review(
            engine,
            principal,
            message_id,
            action=action,
            note=note,
            request_id=request_id,
        )
    with Session(engine) as session:
        message = session.get(Message, message_id)
        if message is None:
            raise ConversationError(404, "REVIEW_MESSAGE_NOT_FOUND", "Review message not found")
        return message.conversation_id
