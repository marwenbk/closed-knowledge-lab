"use client";

import { useCustomMutation, useGetIdentity, useInvalidate, useOne } from "@refinedev/core";
import { Bot, Check, CircleUserRound, FileSearch, LockKeyhole, MessageSquareText, RefreshCw, UserRoundX } from "lucide-react";
import { useParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import {
  ErrorState,
  formatDate,
  LoadingState,
  PageHeading,
  Panel,
  StatusBadge,
} from "@/components/admin-ui";
import { Button } from "@/components/ui/button";
import type { AdminConversation, AdminIdentity, FeedbackCategory, RagRunDetail } from "@/lib/admin-types";

const REVIEW_ROLES = new Set(["ADMIN", "SUPERVISOR", "HUMAN_REVIEWER"]);
const FEEDBACK_CATEGORIES: FeedbackCategory[] = [
  "CORRECT",
  "INCORRECT",
  "MISSING_KB_INFORMATION",
  "CONFLICTING_KB_INFORMATION",
  "RETRIEVAL_FAILURE",
  "GROUNDING_FAILURE",
  "ESCALATION_APPROPRIATE",
  "ESCALATION_UNNECESSARY",
];

function RagInspector({ runId }: { runId: string }) {
  const { result, query } = useOne<RagRunDetail>({ resource: "rag-runs", id: runId });
  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ErrorState error={query.error} />;
  if (!result) return null;
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap gap-2">
        <StatusBadge value={result.status} />
        <StatusBadge value={result.answerability_status} />
        <StatusBadge value={result.verification_status} />
      </div>
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-slate-500">Original question</dt>
          <dd className="mt-1 font-medium">{result.original_query}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Retrieval query</dt>
          <dd className="mt-1 font-medium">{result.retrieval_query}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Modelo</dt>
          <dd className="mt-1 font-mono text-xs">
            {result.model.provider}/{result.model.name}@{result.model.version ?? "unknown"}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">Knowledge base</dt>
          <dd className="mt-1 font-mono text-xs">
            {result.knowledge.dataset_id}:{result.knowledge.dataset_version}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">Latency</dt>
          <dd className="mt-1 font-medium">
            {result.latency_ms === null ? "—" : `${Math.round(result.latency_ms)} ms`}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">Delivered citations</dt>
          <dd className="mt-1 font-medium">{result.citations.length}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Prompt / settings</dt>
          <dd className="mt-1 font-mono text-xs">
            {result.model.prompt_version} / {result.model.settings_version}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">Embedding</dt>
          <dd className="mt-1 truncate font-mono text-xs">{result.model.embedding_version}</dd>
        </div>
      </dl>
      <details className="rounded-xl border border-slate-200">
        <summary className="cursor-pointer px-4 py-3 text-sm font-semibold">
          Structured operational trace
        </summary>
        <pre className="max-h-[32rem] overflow-auto border-t border-slate-200 bg-slate-950 p-4 text-xs leading-5 text-slate-100">
          {JSON.stringify(result.trace, null, 2)}
        </pre>
      </details>
      <p className="text-xs text-slate-500">
        The trace contains verifiable results and decisions, not internal model reasoning.
      </p>
    </div>
  );
}

export default function AdminConversationPage() {
  const { id } = useParams<{ id: string }>();
  const [content, setContent] = useState("");
  const [visibility, setVisibility] = useState<"PUBLIC" | "INTERNAL">("PUBLIC");
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [reviewContent, setReviewContent] = useState("");
  const [feedbackCategory, setFeedbackCategory] = useState<FeedbackCategory>("CORRECT");
  const [feedbackNote, setFeedbackNote] = useState("");
  const identity = useGetIdentity<AdminIdentity>();
  const { result: conversation, query } = useOne<AdminConversation>({
    resource: "conversations",
    id,
    liveMode: "auto",
  });
  const action = useCustomMutation();
  const invalidate = useInvalidate();

  async function refresh() {
    await Promise.all([
      invalidate({ resource: "conversations", id, invalidates: ["detail"] }),
      invalidate({ resource: "handoffs", invalidates: ["list"] }),
      invalidate({ resource: "dashboard", invalidates: ["all"] }),
    ]);
  }

  async function mutate(path: string, values: Record<string, unknown>, success: string) {
    await action.mutateAsync({
      url: `/api/v1/admin/conversations/${id}/${path}`,
      method: "post",
      values,
      successNotification: { message: success, type: "success" },
      errorNotification: (error) => ({
        message: "The operation could not be completed.",
        description: error?.message,
        type: "error",
      }),
    });
    await refresh();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await mutate(
      "messages",
      { content, visibility, client_message_id: crypto.randomUUID() },
      visibility === "PUBLIC" ? "Response sent." : "Internal note saved.",
    );
    setContent("");
  }

  async function review(messageId: string, reviewAction: string, edited?: string) {
    await action.mutateAsync({
      url: `/api/v1/admin/messages/${messageId}/review`,
      method: "post",
      values: { action: reviewAction, content: edited || null, note: null },
      successNotification: { message: "Review decision recorded.", type: "success" },
      errorNotification: (error) => ({ message: "The review could not be completed.", description: error?.message, type: "error" }),
    });
    setReviewContent("");
    await refresh();
  }

  async function submitFeedback(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeRun) return;
    await action.mutateAsync({
      url: "/api/v1/admin/feedback",
      method: "post",
      values: { rag_run_id: activeRun, category: feedbackCategory, note: feedbackNote || null },
      successNotification: { message: "Feedback recorded without changing the system automatically.", type: "success" },
      errorNotification: (error) => ({ message: "The feedback was not recorded.", description: error?.message, type: "error" }),
    });
    setFeedbackNote("");
    await invalidate({ resource: "feedback", invalidates: ["list"] });
  }

  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ErrorState error={query.error} />;
  if (!conversation) return null;
  const activeRun = selectedRun ?? conversation.rag_runs.at(-1)?.rag_run_id ?? null;
  const proposal = conversation.messages.find((message) => ["PENDING", "REGENERATING"].includes(message.review_status));
  const canReview = identity.data?.roles.some((role) => REVIEW_ROLES.has(role)) ?? false;

  return (
    <>
      <PageHeading
        title="Conversation"
        description={`Aberta em ${formatDate(conversation.created_at)} · ${conversation.conversation_id}`}
        actions={
          <div className="flex flex-wrap gap-2">
            <StatusBadge value={conversation.priority} />
            <StatusBadge value={conversation.state} />
          </div>
        }
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(22rem,.65fr)]">
        <div className="grid content-start gap-5">
          {proposal ? (
            <Panel className="border-sky-200 bg-sky-50/40">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="font-semibold">Response awaiting review</h2>
                <StatusBadge value={proposal.review_status} />
              </div>
              <textarea
                className="mt-4 min-h-36 w-full rounded-xl border border-sky-200 bg-white p-3 text-sm leading-6"
                disabled={!canReview || proposal.review_status === "REGENERATING"}
                onChange={(event) => setReviewContent(event.target.value)}
                value={reviewContent || proposal.content}
              />
              <p className="mt-2 text-xs text-slate-500">Every edit is checked against the citations again before delivery.</p>
              {canReview ? (
                <div className="mt-4 flex flex-wrap gap-2">
                  <Button disabled={action.mutation.isPending} onClick={() => void review(proposal.message_id, "APPROVE")} type="button"><Check size={16} /> Aprovar</Button>
                  <Button disabled={action.mutation.isPending} onClick={() => void review(proposal.message_id, "EDIT_AND_SEND", reviewContent || proposal.content)} type="button" variant="outline">Editar e enviar</Button>
                  <Button disabled={action.mutation.isPending || proposal.review_regeneration_count >= 1} onClick={() => void review(proposal.message_id, "REJECT_AND_REGENERATE")} type="button" variant="outline"><RefreshCw size={16} /> Regenerate once</Button>
                  <Button disabled={action.mutation.isPending} onClick={() => void review(proposal.message_id, "REJECT_AND_TAKEOVER")} type="button" variant="outline"><UserRoundX size={16} /> Send to support</Button>
                  <Button disabled={action.mutation.isPending} onClick={() => void review(proposal.message_id, "CLOSE")} type="button" variant="danger">Close without sending</Button>
                </div>
              ) : null}
            </Panel>
          ) : null}
          <Panel>
            <div className="mb-4 flex items-center justify-between gap-3">
              <h2 className="font-semibold">Public history</h2>
              <span className="text-xs text-slate-500">
                {conversation.assigned_agent_name ?? "No assigned agent"}
              </span>
            </div>
            <div className="grid max-h-[36rem] gap-3 overflow-y-auto pr-1">
              {conversation.messages
                .filter((message) => message.visibility === "PUBLIC")
                .map((message) => {
                  const operator = message.sender_type === "HUMAN";
                  const assistant = message.sender_type === "AI";
                  return (
                    <article
                      className={`max-w-[88%] rounded-2xl p-3 text-sm ${
                        message.sender_type === "CUSTOMER"
                          ? "justify-self-end bg-teal-700 text-white"
                          : "justify-self-start border border-slate-200 bg-slate-50"
                      }`}
                      key={message.message_id}
                    >
                      <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold opacity-75">
                        {assistant ? <Bot size={13} /> : <CircleUserRound size={13} />}
                        {message.sender_label}
                        {operator ? " · human support" : ""}
                      </p>
                      <p className="whitespace-pre-wrap leading-6">{message.content}</p>
                      <p className="mt-1 text-[11px] opacity-65">{formatDate(message.created_at)}</p>
                    </article>
                  );
                })}
            </div>
          </Panel>

          <Panel>
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-semibold">Responder ou anotar</h2>
              <div className="flex rounded-lg bg-slate-100 p-1 text-xs font-medium">
                {(["PUBLIC", "INTERNAL"] as const).map((mode) => (
                  <button
                    className={`rounded-md px-3 py-1.5 ${
                      visibility === mode ? "bg-white shadow-sm" : "text-slate-500"
                    }`}
                    key={mode}
                    onClick={() => setVisibility(mode)}
                    type="button"
                  >
                    {mode === "PUBLIC" ? "Public response" : "Internal note"}
                  </button>
                ))}
              </div>
            </div>
            <form className="grid gap-3" onSubmit={(event) => void submit(event)}>
              <textarea
                className="min-h-28 resize-y rounded-xl border border-slate-300 p-3 text-sm outline-none focus:border-teal-600 focus:ring-2 focus:ring-teal-100"
                maxLength={4000}
                onChange={(event) => setContent(event.target.value)}
                placeholder={
                  visibility === "PUBLIC"
                    ? "Write the response to the customer…"
                    : "Record context for the team only…"
                }
                required
                value={content}
              />
              <div className="flex flex-wrap justify-between gap-2">
                <p className="flex items-center gap-1.5 text-xs text-slate-500">
                  {visibility === "INTERNAL" ? <LockKeyhole size={13} /> : <MessageSquareText size={13} />}
                  {visibility === "INTERNAL"
                    ? "Never appears in the widget or model context."
                    : "Visible in the widget after delivery."}
                </p>
                <Button
                  disabled={action.mutation.isPending || !content.trim()}
                  type="submit"
                >
                  {visibility === "PUBLIC" ? "Send" : "Save note"}
                </Button>
              </div>
            </form>
          </Panel>
        </div>

        <div className="grid content-start gap-5">
          <Panel>
            <h2 className="mb-4 font-semibold">Conversation control</h2>
            <dl className="grid gap-3 text-sm">
              <div>
                <dt className="text-slate-500">Motivo</dt>
                <dd className="font-medium">{conversation.handoff_reason ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Pedido</dt>
                <dd className="font-medium">{formatDate(conversation.handoff_requested_at)}</dd>
              </div>
            </dl>
            <div className="mt-5 grid gap-2 sm:grid-cols-3 xl:grid-cols-1">
              {conversation.state === "HUMAN_REQUESTED" ? (
                <Button
                  onClick={() => void mutate("claim", {}, "Handoff assigned.")}
                  type="button"
                >
                  Claim handoff
                </Button>
              ) : null}
              {!["RETURNED_TO_AI", "CLOSED"].includes(conversation.state) ? (
                <Button
                  onClick={() => void mutate("return-to-ai", {}, "Conversation returned to AI.")}
                  type="button"
                  variant="outline"
                >
                  Return to AI
                </Button>
              ) : null}
              {conversation.state !== "CLOSED" ? (
                <Button
                  onClick={() => void mutate("close", {}, "Conversation closed.")}
                  type="button"
                  variant="danger"
                >
                  Close conversation
                </Button>
              ) : null}
            </div>
          </Panel>

          <Panel>
            <div className="mb-4 flex items-center gap-2">
              <LockKeyhole className="text-amber-600" size={18} />
              <h2 className="font-semibold">Notas internas</h2>
            </div>
            <div className="grid gap-3">
              {conversation.messages.filter((message) => message.sender_type === "INTERNAL").length ===
              0 ? (
                <p className="text-sm text-slate-500">No internal notes.</p>
              ) : (
                conversation.messages
                  .filter((message) => message.sender_type === "INTERNAL")
                  .map((message) => (
                    <article className="rounded-xl bg-amber-50 p-3 text-sm" key={message.message_id}>
                      <p className="font-medium">{message.sender_label}</p>
                      <p className="mt-1 whitespace-pre-wrap text-slate-700">{message.content}</p>
                    </article>
                  ))
              )}
            </div>
          </Panel>
        </div>
      </div>

      <Panel className="mt-6">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <FileSearch className="text-teal-700" size={20} />
            <h2 className="font-semibold">Inspetor RAG</h2>
          </div>
          {conversation.rag_runs.length ? (
            <select
              className="max-w-full rounded-lg border border-slate-300 px-3 py-2 text-xs"
              onChange={(event) => setSelectedRun(event.target.value)}
              value={activeRun ?? ""}
            >
              {conversation.rag_runs.map((run) => (
                <option key={run.rag_run_id} value={run.rag_run_id}>
                  {run.rag_run_id} · {run.answerability_status ?? run.status}
                </option>
              ))}
            </select>
          ) : null}
        </div>
        {activeRun ? (
          <div className="grid gap-5">
            <RagInspector runId={activeRun} />
            <form className="grid gap-3 rounded-xl border border-slate-200 p-4" onSubmit={(event) => void submitFeedback(event)}>
              <h3 className="font-semibold">Classify this response</h3>
              <div className="grid gap-3 md:grid-cols-[minmax(14rem,.45fr)_1fr_auto]">
                <select className="rounded-xl border border-slate-300 px-3 py-2 text-sm" onChange={(event) => setFeedbackCategory(event.target.value as FeedbackCategory)} value={feedbackCategory}>
                  {FEEDBACK_CATEGORIES.map((category) => <option key={category} value={category}>{category}</option>)}
                </select>
                <input className="rounded-xl border border-slate-300 px-3 py-2 text-sm" maxLength={2000} onChange={(event) => setFeedbackNote(event.target.value)} placeholder="Optional note" value={feedbackNote} />
                <Button disabled={action.mutation.isPending} type="submit">Record feedback</Button>
              </div>
              <p className="text-xs text-slate-500">Feedback is append-only and never changes the knowledge base, prompts, or model automatically.</p>
            </form>
          </div>
        ) : (
          <p className="text-sm text-slate-500">This conversation has no RAG run yet.</p>
        )}
      </Panel>
    </>
  );
}
