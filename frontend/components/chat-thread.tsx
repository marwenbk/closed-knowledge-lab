"use client";

import {
  AuiIf,
  ComposerPrimitive,
  MessagePartPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import {
  ArrowDown,
  CheckCircle2,
  FileText,
  LoaderCircle,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  UserRound,
  WifiOff,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { readCitations, useWidgetRuntime } from "@/providers/widget-runtime";

const STARTERS = [
  "How much does the Family plan cost per month?",
  "How many dependents does the Family plan allow?",
  "How does the refund policy work?",
];

function MessageText() {
  return (
    <p className="whitespace-pre-wrap text-[0.94rem] leading-6">
      <MessagePartPrimitive.Text />
    </p>
  );
}

function MessageTime() {
  const createdAt = useAuiState((state) => state.message.createdAt);
  if (!createdAt) return null;
  return (
    <time className="mt-2 block text-[0.7rem] text-slate-400" dateTime={createdAt.toISOString()}>
      {new Intl.DateTimeFormat("en-US", { hour: "2-digit", minute: "2-digit" }).format(
        createdAt,
      )}
    </time>
  );
}

function CitationList() {
  const metadata = useAuiState((state) => state.message.metadata.custom);
  const citations = readCitations(metadata?.citations);
  if (citations.length === 0) return null;
  return (
    <details className="mt-3 rounded-xl border border-teal-100 bg-teal-50/70 text-sm">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 font-semibold text-teal-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-teal-600">
        <FileText aria-hidden="true" className="size-4" />
        {citations.length === 1 ? "1 source consulted" : `${citations.length} sources consulted`}
      </summary>
      <div className="space-y-2 border-t border-teal-100 p-3">
        {citations.map((citation) => (
          <blockquote key={citation.citation_id} className="border-l-2 border-teal-500 pl-3">
            <p className="font-semibold text-slate-800">{citation.document}</p>
            <p className="text-xs text-slate-500">{citation.section}</p>
            <p className="mt-1 text-sm leading-5 text-slate-600">“{citation.quote}”</p>
          </blockquote>
        ))}
      </div>
    </details>
  );
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="mx-auto flex w-full max-w-3xl justify-end px-4 py-2 sm:px-6">
      <div className="max-w-[86%] rounded-2xl rounded-br-md bg-slate-900 px-4 py-3 text-white shadow-sm sm:max-w-[72%]">
        <MessagePrimitive.Parts components={{ Text: MessageText }} />
        <MessageTime />
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  const metadata = useAuiState((state) => state.message.metadata.custom);
  const hasContent = useAuiState((state) =>
    state.message.content.some((part) => part.type !== "text" || part.text.trim()),
  );
  const senderLabel =
    typeof metadata?.senderLabel === "string" ? metadata.senderLabel : "Closed-Knowledge Lab";
  const isHuman = metadata?.senderType === "HUMAN";
  if (!hasContent) return null;
  return (
    <MessagePrimitive.Root className="mx-auto flex w-full max-w-3xl gap-3 px-4 py-2 sm:px-6">
      <div className="mt-1 flex size-8 shrink-0 items-center justify-center rounded-xl bg-teal-700 text-white shadow-sm">
        {isHuman ? <ShieldCheck className="size-4" /> : <Sparkles className="size-4" />}
      </div>
      <div className="max-w-[calc(100%-2.75rem)] rounded-2xl rounded-tl-md border border-slate-200 bg-white px-4 py-3 text-slate-700 shadow-sm sm:max-w-[78%]">
        <p className="mb-1 text-xs font-bold uppercase tracking-[0.12em] text-teal-700">
          {senderLabel}
        </p>
        <MessagePrimitive.Parts components={{ Text: MessageText }} />
        <CitationList />
        <MessageTime />
      </div>
    </MessagePrimitive.Root>
  );
}

function SystemMessage() {
  return (
    <MessagePrimitive.Root className="mx-auto w-full max-w-3xl px-6 py-2 text-center text-xs text-slate-500">
      <MessagePrimitive.Parts components={{ Text: MessageText }} />
    </MessagePrimitive.Root>
  );
}

function Welcome() {
  const aui = useAui();
  const sendStarter = (starter: string) => {
    aui.composer().setText(starter);
    aui.composer().send();
  };
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-5 py-10 sm:px-8">
      <div className="mb-6 flex size-12 items-center justify-center rounded-2xl bg-teal-700 text-white shadow-lg shadow-teal-900/10">
        <Sparkles className="size-5" />
      </div>
      <h2 className="text-balance text-2xl font-bold tracking-tight text-slate-950 sm:text-3xl">
        How can I help today?
      </h2>
      <p className="mt-2 max-w-xl text-sm leading-6 text-slate-500">
        Ask about plans, dependents, consultations, cancellations, and policies for the fictional service.
      </p>
      <div className="mt-7 grid gap-2 sm:grid-cols-3">
        {STARTERS.map((starter) => (
          <button
            className="rounded-2xl border border-slate-200 bg-white p-3 text-left text-sm leading-5 text-slate-700 shadow-sm transition hover:-translate-y-0.5 hover:border-teal-300 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600"
            key={starter}
            onClick={() => sendStarter(starter)}
            type="button"
          >
            {starter}
          </button>
        ))}
      </div>
    </div>
  );
}

function Composer() {
  const { conversationState } = useWidgetRuntime();
  const humanControlled = ["HUMAN_REQUESTED", "HUMAN_ASSIGNED", "HUMAN_ACTIVE"].includes(
    conversationState ?? "",
  );
  const reviewPending = conversationState === "AI_REVIEW_PENDING";
  return (
    <div className="mx-auto w-full max-w-3xl px-3 pb-3 sm:px-6 sm:pb-5">
      <ComposerPrimitive.Root className="flex items-end gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-[0_14px_40px_rgba(15,23,42,0.10)] focus-within:border-teal-500 focus-within:ring-2 focus-within:ring-teal-100">
        <ComposerPrimitive.Input
          aria-label={
            humanControlled
              ? "Message human support"
              : "Message Closed-Knowledge Lab"
          }
          className="max-h-32 min-h-11 flex-1 resize-none bg-transparent px-2 py-2.5 text-[0.95rem] leading-5 text-slate-900 outline-none placeholder:text-slate-400"
          disabled={reviewPending}
          placeholder={
            reviewPending
              ? "Wait for the answer review…"
              : humanControlled
                ? "Write to human support…"
                : "Write your question…"
          }
          rows={1}
        />
        <ComposerPrimitive.Send asChild>
          <Button aria-label="Send message" className="size-11 rounded-xl p-0" disabled={reviewPending} type="submit">
            <Send aria-hidden="true" className="size-4" />
          </Button>
        </ComposerPrimitive.Send>
      </ComposerPrimitive.Root>
      <p className="mt-2 px-2 text-center text-[0.68rem] leading-4 text-slate-400">
        Fictional demonstration service. Do not submit sensitive medical or personal data.
      </p>
    </div>
  );
}

function RuntimeNotice() {
  const isRunning = useAuiState((state) => state.thread.isRunning);
  const {
    connectionStatus,
    conversationState,
    dismissError,
    error,
    isReady,
    retryLastSubmission,
  } = useWidgetRuntime();
  if (error) {
    return (
      <div className="mx-auto mb-2 flex w-[calc(100%-1.5rem)] max-w-3xl items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-sm text-rose-800">
        <WifiOff className="mt-0.5 size-4 shrink-0" />
        <p className="flex-1">{error}</p>
        {retryLastSubmission ? (
          <button
            className="font-bold underline"
            onClick={() => void retryLastSubmission()}
            type="button"
          >
            Try again
          </button>
        ) : !isReady ? (
          <button className="font-bold underline" onClick={dismissError} type="button">
            Reconnect
          </button>
        ) : (
          <button aria-label="Close notice" onClick={dismissError} type="button">
            <X className="size-4" />
          </button>
        )}
      </div>
    );
  }
  if (conversationState === "CLOSED") {
    return (
      <div className="mx-auto mb-2 flex w-[calc(100%-1.5rem)] max-w-3xl items-center gap-2 rounded-xl bg-slate-100 px-3 py-2 text-sm text-slate-600">
        <CheckCircle2 className="size-4 text-teal-700" /> Conversation closed.
      </div>
    );
  }
  if (conversationState === "AI_REVIEW_PENDING") {
    return (
      <div className="mx-auto mb-2 w-[calc(100%-1.5rem)] max-w-3xl rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-800">
        The answer passed automated verification and is waiting for human review before delivery.
      </div>
    );
  }
  if (conversationState === "HUMAN_REQUESTED") {
    return (
      <div className="mx-auto mb-2 w-[calc(100%-1.5rem)] max-w-3xl rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
        Your request was sent. You can continue writing while you wait for a person.
      </div>
    );
  }
  if (conversationState === "HUMAN_ASSIGNED") {
    return (
      <div className="mx-auto mb-2 w-[calc(100%-1.5rem)] max-w-3xl rounded-xl border border-teal-200 bg-teal-50 px-3 py-2 text-sm text-teal-800">
        A team member has taken over the conversation.
      </div>
    );
  }
  if (conversationState === "HUMAN_ACTIVE") {
    return (
      <div className="mx-auto mb-2 w-[calc(100%-1.5rem)] max-w-3xl rounded-xl border border-teal-200 bg-teal-50 px-3 py-2 text-sm text-teal-800">
        You are speaking with a team member.
      </div>
    );
  }
  if (conversationState === "RETURNED_TO_AI") {
    return (
      <div className="mx-auto mb-2 w-[calc(100%-1.5rem)] max-w-3xl px-3 text-xs text-teal-700">
        Closed-Knowledge Lab will answer the next messages.
      </div>
    );
  }
  if (isRunning) {
    return (
      <div className="mx-auto mb-2 flex w-[calc(100%-1.5rem)] max-w-3xl items-center gap-2 px-3 text-xs font-medium text-slate-500">
        <LoaderCircle className="size-3.5 animate-spin text-teal-700" /> Consulting approved evidence…
      </div>
    );
  }
  if (connectionStatus === "polling") {
    return (
      <div className="mx-auto mb-2 w-[calc(100%-1.5rem)] max-w-3xl px-3 text-xs text-amber-700">
        Realtime updates are unstable; synchronizing automatically.
      </div>
    );
  }
  return null;
}

export function ChatThread() {
  return (
    <ThreadPrimitive.Root className="relative flex min-h-0 flex-1 flex-col bg-slate-50/80">
      <ThreadPrimitive.Viewport className="flex min-h-0 flex-1 flex-col overflow-y-auto pt-2">
        <AuiIf condition={(state) => state.thread.isEmpty}>
          <Welcome />
        </AuiIf>
        <ThreadPrimitive.Messages>
          {({ message }) => {
            if (message.role === "user") return <UserMessage />;
            if (message.role === "system") return <SystemMessage />;
            return <AssistantMessage />;
          }}
        </ThreadPrimitive.Messages>
        <div className="min-h-4 flex-1" />
        <ThreadPrimitive.ViewportFooter className="sticky bottom-0 z-10 bg-gradient-to-t from-slate-50 via-slate-50/95 to-transparent pt-7">
          <ThreadPrimitive.ScrollToBottom asChild>
            <Button
              aria-label="Go to the latest message"
              className="mx-auto mb-2 flex rounded-full shadow-sm"
              size="icon"
              variant="outline"
            >
              <ArrowDown className="size-4" />
            </Button>
          </ThreadPrimitive.ScrollToBottom>
          <RuntimeNotice />
          <Composer />
        </ThreadPrimitive.ViewportFooter>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}

export function ConversationActions() {
  const { closeCurrentConversation, conversationState, requestHuman, restartConversation } =
    useWidgetRuntime();
  const handoffRequested = ["HUMAN_REQUESTED", "HUMAN_ASSIGNED", "HUMAN_ACTIVE"].includes(
    conversationState ?? "",
  );
  return (
    <div className="flex items-center gap-1">
      <Button
        aria-label="Talk to a person"
        disabled={!conversationState || conversationState === "CLOSED" || handoffRequested}
        onClick={() => void requestHuman()}
        size="sm"
        type="button"
        variant="ghost"
      >
        <UserRound aria-hidden="true" className="size-3.5" />
        <span className="hidden sm:inline">Talk to a person</span>
      </Button>
      <Button
        aria-label="New conversation"
        onClick={() => void restartConversation()}
        size="sm"
        type="button"
        variant="ghost"
      >
        <RotateCcw aria-hidden="true" className="size-3.5" />
        <span className="hidden sm:inline">New conversation</span>
      </Button>
      <Button
        aria-label="Close conversation"
        disabled={!conversationState || conversationState === "CLOSED"}
        onClick={() => void closeCurrentConversation()}
        size="sm"
        type="button"
        variant="danger"
      >
        Close
      </Button>
    </div>
  );
}

export function ConnectionDot() {
  const { connectionStatus } = useWidgetRuntime();
  const label = {
    connected: "Conectado",
    connecting: "Conectando",
    offline: "Offline",
    polling: "Sincronizando",
  }[connectionStatus];
  return (
    <span className="flex items-center gap-1.5 text-[0.68rem] font-medium text-slate-500">
      <span
        aria-hidden="true"
        className={cn(
          "size-1.5 rounded-full",
          connectionStatus === "connected" ? "bg-emerald-500" : "bg-amber-500",
        )}
      />
      {label}
    </span>
  );
}
