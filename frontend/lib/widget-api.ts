import type {
  ApiErrorBody,
  Conversation,
  HandoffResponse,
  MessageSubmissionResponse,
  WidgetSession,
} from "@/lib/widget-types";

export class WidgetApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "WidgetApiError";
  }
}

type RequestOptions = {
  method?: "GET" | "POST";
  token?: string;
  body?: Record<string, unknown>;
  signal?: AbortSignal;
};

export function normalizeApiBaseUrl(value: string): string {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
    throw new Error("The widget API URL is invalid.");
  }
  url.pathname = url.pathname.replace(/\/$/, "");
  url.search = "";
  url.hash = "";
  return url.toString().replace(/\/$/, "");
}

async function request<T>(
  apiBaseUrl: string,
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers = new Headers({ Accept: "application/json" });
  if (options.body) headers.set("Content-Type", "application/json");
  if (options.token) headers.set("Authorization", `Bearer ${options.token}`);

  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined,
    cache: "no-store",
    signal: options.signal,
  });
  if (!response.ok) {
    let body: ApiErrorBody = {};
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      // The stable fallback below avoids exposing upstream HTML or proxy errors.
    }
    throw new WidgetApiError(
      response.status,
      body.error?.code ?? "REQUEST_FAILED",
      body.error?.message ?? "The request could not be completed.",
      body.error?.request_id,
    );
  }
  return (await response.json()) as T;
}

export function createWidgetSession(
  apiBaseUrl: string,
  assistantKey: string,
  locale: string,
): Promise<WidgetSession> {
  return request(apiBaseUrl, "/api/v1/widget/sessions", {
    method: "POST",
    body: { assistant_key: assistantKey, locale },
  });
}

export function createOrRestoreConversation(
  apiBaseUrl: string,
  token: string,
  conversationId?: string,
): Promise<Conversation> {
  return request(apiBaseUrl, "/api/v1/widget/conversations", {
    method: "POST",
    token,
    body: conversationId ? { conversation_id: conversationId } : {},
  });
}

export function getConversation(
  apiBaseUrl: string,
  token: string,
  conversationId: string,
  signal?: AbortSignal,
): Promise<Conversation> {
  return request(apiBaseUrl, `/api/v1/widget/conversations/${conversationId}`, {
    token,
    signal,
  });
}

export function sendConversationMessage(
  apiBaseUrl: string,
  token: string,
  conversationId: string,
  content: string,
  clientMessageId: string,
): Promise<MessageSubmissionResponse> {
  return request(apiBaseUrl, `/api/v1/widget/conversations/${conversationId}/messages`, {
    method: "POST",
    token,
    body: { content, client_message_id: clientMessageId },
  });
}

export function requestHumanSupport(
  apiBaseUrl: string,
  token: string,
  conversationId: string,
): Promise<HandoffResponse> {
  return request(apiBaseUrl, `/api/v1/widget/conversations/${conversationId}/request-human`, {
    method: "POST",
    token,
  });
}

export function closeConversation(
  apiBaseUrl: string,
  token: string,
  conversationId: string,
): Promise<Conversation> {
  return request(apiBaseUrl, `/api/v1/widget/conversations/${conversationId}/close`, {
    method: "POST",
    token,
  });
}
