"use client";

import {
  AssistantRuntimeProvider,
  type AppendMessage,
  type ThreadMessageLike,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { subscribeToConversation } from "@/lib/sse";
import {
  closeConversation,
  createOrRestoreConversation,
  createWidgetSession,
  getConversation,
  normalizeApiBaseUrl,
  requestHumanSupport,
  sendConversationMessage,
  WidgetApiError,
} from "@/lib/widget-api";
import type {
  Citation,
  Conversation,
  ConversationState,
  PublicMessage,
  WidgetSession,
} from "@/lib/widget-types";

type ConnectionStatus = "connecting" | "connected" | "polling" | "offline";

type RuntimeMessage = PublicMessage & { optimistic?: boolean };

type FailedSubmission = {
  content: string;
  clientMessageId: string;
};

type WidgetRuntimeContextValue = {
  closeCurrentConversation: () => Promise<void>;
  connectionStatus: ConnectionStatus;
  conversationState: ConversationState | null;
  dismissError: () => void;
  error: string | null;
  isReady: boolean;
  requestHuman: () => Promise<void>;
  restartConversation: () => Promise<void>;
  retryLastSubmission: (() => Promise<void>) | null;
};

const WidgetRuntimeContext = createContext<WidgetRuntimeContextValue | null>(null);

function storageKey(apiBaseUrl: string, assistantKey: string, item: string): string {
  return `topmed:${apiBaseUrl}:${assistantKey}:${item}`;
}

function readSession(key: string): WidgetSession | null {
  try {
    const stored = sessionStorage.getItem(key);
    if (!stored) return null;
    const session = JSON.parse(stored) as WidgetSession;
    if (new Date(session.expires_at).getTime() <= Date.now() + 30_000) return null;
    return session;
  } catch {
    return null;
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof WidgetApiError) {
    const messages: Record<string, string> = {
      CONVERSATION_CLOSED: "This conversation is closed. Start a new conversation to continue.",
      IDEMPOTENCY_KEY_REUSED: "The message could not be retried safely.",
      INVALID_ASSISTANT_KEY: "This assistant is not available at this address.",
      INVALID_WIDGET_SESSION: "Your session expired. Start a new conversation.",
      LLM_NOT_READY: "The assistant is temporarily unavailable. Try again shortly.",
      MESSAGE_FAILED: "This message could not be processed. Send a new attempt.",
    };
    return messages[error.code] ?? error.message;
  }
  return "Could not connect to Closed-Knowledge Lab. Check your connection and try again.";
}

function optimisticMessage(content: string, clientMessageId: string): RuntimeMessage {
  const now = new Date().toISOString();
  return {
    message_id: `optimistic:${clientMessageId}`,
    rag_run_id: null,
    sender: { type: "CUSTOMER", label: "You" },
    content,
    status: "PENDING",
    citations: [],
    created_at: now,
    delivered_at: null,
    optimistic: true,
  };
}

function convertMessage(message: RuntimeMessage): ThreadMessageLike {
  const role =
    message.sender.type === "CUSTOMER"
      ? "user"
      : message.sender.type === "SYSTEM"
        ? "system"
        : "assistant";
  return {
    id: message.message_id,
    role,
    content: [{ type: "text", text: message.content }],
    createdAt: new Date(message.created_at),
    status:
      role === "assistant"
        ? message.status === "FAILED"
          ? { type: "incomplete", reason: "error" }
          : { type: "complete", reason: "stop" }
        : undefined,
    metadata: {
      custom: {
        citations: message.citations,
        deliveredAt: message.delivered_at,
        optimistic: message.optimistic ?? false,
        ragRunId: message.rag_run_id,
        senderLabel: message.sender.label,
        senderType: message.sender.type,
        status: message.status,
      },
    },
  };
}

export function WidgetRuntimeProvider({
  apiBaseUrl: rawApiBaseUrl,
  assistantKey,
  locale = "en-US",
  children,
}: PropsWithChildren<{
  apiBaseUrl: string;
  assistantKey: string;
  locale?: string;
}>) {
  const apiBaseUrl = useMemo(() => normalizeApiBaseUrl(rawApiBaseUrl), [rawApiBaseUrl]);
  const sessionStorageKey = storageKey(apiBaseUrl, assistantKey, "session");
  const conversationStorageKey = storageKey(apiBaseUrl, assistantKey, "conversation");
  const cursorStorageKey = storageKey(apiBaseUrl, assistantKey, "cursor");
  const sessionRef = useRef<WidgetSession | null>(null);
  const conversationRef = useRef<Conversation | null>(null);
  const [messages, setMessages] = useState<RuntimeMessage[]>([]);
  const [conversationState, setConversationState] = useState<ConversationState | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("connecting");
  const [isReady, setIsReady] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [failedSubmission, setFailedSubmission] = useState<FailedSubmission | null>(null);
  const [streamGeneration, setStreamGeneration] = useState(0);

  const applyConversation = useCallback((conversation: Conversation) => {
    conversationRef.current = conversation;
    setMessages(conversation.messages);
    setConversationState(conversation.state);
  }, []);

  const refreshConversation = useCallback(
    async (signal?: AbortSignal) => {
      const session = sessionRef.current;
      const conversation = conversationRef.current;
      if (!session || !conversation) return;
      applyConversation(
        await getConversation(apiBaseUrl, session.token, conversation.conversation_id, signal),
      );
    },
    [apiBaseUrl, applyConversation],
  );

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    async function establish(): Promise<{ session: WidgetSession; conversation: Conversation }> {
      let session = readSession(sessionStorageKey);
      if (!session) {
        session = await createWidgetSession(apiBaseUrl, assistantKey, locale);
        sessionStorage.setItem(sessionStorageKey, JSON.stringify(session));
      }
      const storedConversationId = sessionStorage.getItem(conversationStorageKey) ?? undefined;
      try {
        const conversation = await createOrRestoreConversation(
          apiBaseUrl,
          session.token,
          storedConversationId,
        );
        return { session, conversation };
      } catch (caught) {
        if (caught instanceof WidgetApiError && caught.status === 404) {
          const conversation = await createOrRestoreConversation(apiBaseUrl, session.token);
          return { session, conversation };
        }
        if (caught instanceof WidgetApiError && caught.status === 401) {
          sessionStorage.removeItem(sessionStorageKey);
          sessionStorage.removeItem(conversationStorageKey);
          session = await createWidgetSession(apiBaseUrl, assistantKey, locale);
          sessionStorage.setItem(sessionStorageKey, JSON.stringify(session));
          const conversation = await createOrRestoreConversation(apiBaseUrl, session.token);
          return { session, conversation };
        }
        throw caught;
      }
    }

    async function connect(): Promise<void> {
      try {
        setConnectionStatus("connecting");
        const established = await establish();
        if (cancelled) return;
        sessionRef.current = established.session;
        applyConversation(established.conversation);
        sessionStorage.setItem(
          conversationStorageKey,
          established.conversation.conversation_id,
        );
        setIsReady(true);
        setError(null);

        let retryDelay = 1_000;
        while (!cancelled) {
          const cursor = Number(sessionStorage.getItem(cursorStorageKey) ?? 0);
          try {
            setConnectionStatus("connected");
            await subscribeToConversation(
              apiBaseUrl,
              established.session.token,
              established.conversation.conversation_id,
              Number.isSafeInteger(cursor) && cursor >= 0 ? cursor : 0,
              controller.signal,
              (type, event) => {
                sessionStorage.setItem(cursorStorageKey, String(event.event_id));
                if (type === "processing.started") setIsRunning(true);
                if (["message.delivered", "conversation.closed"].includes(type)) {
                  setIsRunning(false);
                }
                if (type === "review.pending") setIsRunning(false);
                if (
                  [
                    "message.created",
                    "message.delivered",
                    "handoff.requested",
                    "handoff.assigned",
                    "handoff.started",
                    "handoff.returned_to_ai",
                    "review.pending",
                    "conversation.closed",
                    "error",
                  ].includes(type)
                ) {
                  if (type.startsWith("handoff.")) setIsRunning(false);
                  void refreshConversation(controller.signal);
                }
              },
            );
          } catch (caught) {
            if (cancelled || controller.signal.aborted) return;
            if (caught instanceof WidgetApiError && caught.status === 401) {
              sessionStorage.removeItem(sessionStorageKey);
              sessionStorage.removeItem(conversationStorageKey);
              sessionStorage.removeItem(cursorStorageKey);
              sessionRef.current = null;
              conversationRef.current = null;
              setStreamGeneration((value) => value + 1);
              return;
            }
            setConnectionStatus("polling");
            try {
              await refreshConversation(controller.signal);
            } catch {
              setConnectionStatus("offline");
            }
            await new Promise((resolve) => window.setTimeout(resolve, retryDelay));
            retryDelay = Math.min(retryDelay * 2, 10_000);
          }
        }
      } catch (caught) {
        if (cancelled) return;
        setConnectionStatus("offline");
        setError(errorMessage(caught));
      }
    }

    void connect();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [
    apiBaseUrl,
    applyConversation,
    assistantKey,
    conversationStorageKey,
    cursorStorageKey,
    locale,
    refreshConversation,
    sessionStorageKey,
    streamGeneration,
  ]);

  const submit = useCallback(
    async (content: string, clientMessageId: string, appendOptimistic: boolean) => {
      const session = sessionRef.current;
      const conversation = conversationRef.current;
      if (!session || !conversation || conversation.state === "CLOSED") return;
      if (appendOptimistic) {
        setMessages((current) => [
          ...current.filter((message) => !message.optimistic),
          optimisticMessage(content, clientMessageId),
        ]);
      }
      setIsRunning(["AI_ACTIVE", "RETURNED_TO_AI"].includes(conversation.state));
      setError(null);
      try {
        await sendConversationMessage(
          apiBaseUrl,
          session.token,
          conversation.conversation_id,
          content,
          clientMessageId,
        );
        await refreshConversation();
        setFailedSubmission(null);
      } catch (caught) {
        setFailedSubmission({ content, clientMessageId });
        setError(errorMessage(caught));
        try {
          await refreshConversation();
        } catch {
          setConnectionStatus("offline");
        }
      } finally {
        setIsRunning(false);
      }
    },
    [apiBaseUrl, refreshConversation],
  );

  const onNew = useCallback(
    async (message: AppendMessage) => {
      const part = message.content.find((item) => item.type === "text");
      if (!part || part.type !== "text" || !part.text.trim()) return;
      await submit(part.text.trim(), crypto.randomUUID(), true);
    },
    [submit],
  );

  const restartConversation = useCallback(async () => {
    const session = sessionRef.current;
    if (!session) return;
    setIsRunning(false);
    setError(null);
    setFailedSubmission(null);
    try {
      sessionStorage.removeItem(conversationStorageKey);
      sessionStorage.removeItem(cursorStorageKey);
      const conversation = await createOrRestoreConversation(apiBaseUrl, session.token);
      applyConversation(conversation);
      sessionStorage.setItem(conversationStorageKey, conversation.conversation_id);
      setStreamGeneration((value) => value + 1);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, [apiBaseUrl, applyConversation, conversationStorageKey, cursorStorageKey]);

  const closeCurrentConversation = useCallback(async () => {
    const session = sessionRef.current;
    const conversation = conversationRef.current;
    if (!session || !conversation || conversation.state === "CLOSED") return;
    try {
      applyConversation(
        await closeConversation(apiBaseUrl, session.token, conversation.conversation_id),
      );
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, [apiBaseUrl, applyConversation]);

  const requestHuman = useCallback(async () => {
    const session = sessionRef.current;
    const conversation = conversationRef.current;
    if (!session || !conversation || conversation.state === "CLOSED") return;
    try {
      setIsRunning(false);
      await requestHumanSupport(apiBaseUrl, session.token, conversation.conversation_id);
      await refreshConversation();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, [apiBaseUrl, refreshConversation]);

  const retryLastSubmission = useMemo(
    () =>
      failedSubmission
        ? () => submit(failedSubmission.content, failedSubmission.clientMessageId, false)
        : null,
    [failedSubmission, submit],
  );
  const dismissError = useCallback(() => {
    setError(null);
    if (!isReady) setStreamGeneration((value) => value + 1);
  }, [isReady]);

  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage,
    isRunning,
    isDisabled: !isReady || conversationState === "CLOSED",
    onNew,
  });

  const context = useMemo<WidgetRuntimeContextValue>(
    () => ({
      closeCurrentConversation,
      connectionStatus,
      conversationState,
      dismissError,
      error,
      isReady,
      requestHuman,
      restartConversation,
      retryLastSubmission,
    }),
    [
      closeCurrentConversation,
      connectionStatus,
      conversationState,
      dismissError,
      error,
      isReady,
      requestHuman,
      restartConversation,
      retryLastSubmission,
    ],
  );

  return (
    <WidgetRuntimeContext.Provider value={context}>
      <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>
    </WidgetRuntimeContext.Provider>
  );
}

export function useWidgetRuntime(): WidgetRuntimeContextValue {
  const context = useContext(WidgetRuntimeContext);
  if (!context) throw new Error("useWidgetRuntime must be used inside WidgetRuntimeProvider");
  return context;
}

export function readCitations(value: unknown): Citation[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is Citation =>
      typeof item === "object" &&
      item !== null &&
      "citation_id" in item &&
      typeof item.citation_id === "string",
  );
}
