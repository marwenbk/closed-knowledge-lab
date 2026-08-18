"use client";

import type {
  AccessControlProvider,
  AuthProvider,
  BaseRecord,
  CrudFilter,
  CustomParams,
  DataProvider,
  GetListParams,
  GetListResponse,
  GetOneParams,
  GetOneResponse,
  HttpError,
  LiveEvent,
  LiveProvider,
} from "@refinedev/core";

import type {
  AdminConversation,
  AdminIdentity,
  AdminStreamEvent,
  Handoff,
  KnowledgeDocument,
} from "@/lib/admin-types";
import { normalizeApiBaseUrl } from "@/lib/widget-api";

const CSRF_COOKIE = "topmed_admin_csrf";
const OPERATOR_ROLES = new Set(["ADMIN", "SUPERVISOR", "SUPPORT_AGENT"]);
const ADMIN_EVENTS = [
  "conversation.ai_resumed",
  "conversation.closed",
  "conversation.created",
  "conversation.state_changed",
  "error",
  "handoff.assigned",
  "handoff.requested",
  "handoff.returned_to_ai",
  "handoff.started",
  "message.created",
  "message.delivered",
  "note.created",
  "processing.started",
] as const;

export const adminApiUrl = normalizeApiBaseUrl(
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000",
);

type ApiErrorBody = {
  error?: { code?: string; message?: string; request_id?: string };
};

export class AdminApiError extends Error implements HttpError {
  readonly statusCode: number;
  readonly code: string;
  readonly requestId?: string;

  constructor(statusCode: number, code: string, message: string, requestId?: string) {
    super(message);
    this.name = "AdminApiError";
    this.statusCode = statusCode;
    this.code = code;
    this.requestId = requestId;
  }
}

function csrfToken(): string | undefined {
  if (typeof document === "undefined") return undefined;
  const prefix = `${CSRF_COOKIE}=`;
  const cookie = document.cookie.split("; ").find((item) => item.startsWith(prefix));
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : undefined;
}

function resolveUrl(path: string, query?: Record<string, unknown>): string {
  const base = new URL(adminApiUrl);
  const url = new URL(path, `${adminApiUrl}/`);
  if (url.origin !== base.origin) {
    throw new AdminApiError(400, "INVALID_ADMIN_URL", "Destino administrativo inválido.");
  }
  Object.entries(query ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  });
  return url.toString();
}

export async function adminRequest<T>(
  path: string,
  options: {
    method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
    body?: unknown;
    query?: Record<string, unknown>;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  const method = options.method ?? "GET";
  const headers = new Headers({ Accept: "application/json" });
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (method !== "GET") {
    const token = csrfToken();
    if (token) headers.set("X-CSRF-Token", token);
  }
  const response = await fetch(resolveUrl(path, options.query), {
    method,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    credentials: "include",
    cache: "no-store",
    signal: options.signal,
  });
  if (!response.ok) {
    let body: ApiErrorBody = {};
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      // Keep upstream HTML and proxy details out of the operator interface.
    }
    throw new AdminApiError(
      response.status,
      body.error?.code ?? "ADMIN_REQUEST_FAILED",
      body.error?.message ?? "Não foi possível concluir a solicitação.",
      body.error?.request_id,
    );
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

let identityPromise: Promise<AdminIdentity> | undefined;

function getIdentity(): Promise<AdminIdentity> {
  identityPromise ??= adminRequest<Omit<AdminIdentity, "id">>("/api/v1/admin/auth/me")
    .then((identity) => ({ ...identity, id: identity.user_id }))
    .catch((error: unknown) => {
      identityPromise = undefined;
      throw error;
    });
  return identityPromise;
}

export const adminAuthProvider: AuthProvider = {
  async login({ email, password }: { email: string; password: string }) {
    try {
      const identity = await adminRequest<Omit<AdminIdentity, "id">>(
        "/api/v1/admin/auth/login",
        { method: "POST", body: { email, password } },
      );
      identityPromise = Promise.resolve({ ...identity, id: identity.user_id });
      return { success: true, redirectTo: "/admin" };
    } catch (error) {
      return { success: false, error: error as Error };
    }
  },
  async logout() {
    try {
      await adminRequest<void>("/api/v1/admin/auth/logout", { method: "POST" });
    } catch (error) {
      if (!(error instanceof AdminApiError) || error.statusCode !== 401) {
        return { success: false, error: error as Error };
      }
    } finally {
      identityPromise = undefined;
    }
    return { success: true, redirectTo: "/admin/login" };
  },
  async check() {
    try {
      await getIdentity();
      return { authenticated: true };
    } catch (error) {
      const unauthorized = error instanceof AdminApiError && [401, 403].includes(error.statusCode);
      return {
        authenticated: false,
        logout: unauthorized,
        redirectTo: "/admin/login",
        error: error as Error,
      };
    }
  },
  async onError(error) {
    const unauthorized = error instanceof AdminApiError && [401, 403].includes(error.statusCode);
    if (unauthorized) identityPromise = undefined;
    return unauthorized ? { logout: true, redirectTo: "/admin/login", error } : { error };
  },
  getIdentity,
  async getPermissions() {
    return (await getIdentity()).roles;
  },
};

export const adminAccessControlProvider: AccessControlProvider = {
  async can() {
    try {
      const identity = await getIdentity();
      const can = identity.roles.some((role) => OPERATOR_ROLES.has(role));
      return { can, reason: can ? undefined : "Função sem acesso operacional." };
    } catch {
      return { can: false, reason: "Sessão administrativa inválida." };
    }
  },
};

function logicalFilter(filters: CrudFilter[] | undefined, field: string): unknown {
  return filters?.find((filter) => "field" in filter && filter.field === field)?.value;
}

function unsupported(): never {
  throw new AdminApiError(405, "METHOD_NOT_ALLOWED", "Operação não suportada.");
}

async function getList<TData extends BaseRecord = BaseRecord>({
  resource,
  pagination,
  filters,
}: GetListParams): Promise<GetListResponse<TData>> {
    const page = pagination?.currentPage ?? 1;
    const limit = pagination?.pageSize ?? 50;
    if (resource === "handoffs") {
      const result = await adminRequest<{ items: Omit<Handoff, "id">[]; total: number }>(
        "/api/v1/admin/handoffs",
        {
          query: {
            offset: (page - 1) * limit,
            limit,
            state: logicalFilter(filters, "state"),
            assignment: logicalFilter(filters, "assignment"),
            priority: logicalFilter(filters, "priority"),
            reason: logicalFilter(filters, "reason"),
          },
        },
      );
      return {
        data: result.items.map((item) => ({
          ...item,
          id: item.conversation_id,
        })) as unknown as TData[],
        total: result.total,
      };
    }
    if (resource === "knowledge-documents") {
      const result = await adminRequest<{ items: KnowledgeDocument[]; total: number }>(
        "/api/v1/admin/knowledge/documents",
        {
          query: {
            offset: (page - 1) * limit,
            limit,
            query: logicalFilter(filters, "query"),
          },
        },
      );
      return { data: result.items as unknown as TData[], total: result.total };
    }
    return unsupported();
}

async function getOne<TData extends BaseRecord = BaseRecord>({
  resource,
  id,
}: GetOneParams): Promise<GetOneResponse<TData>> {
    if (resource === "conversations") {
      const result = await adminRequest<Omit<AdminConversation, "id">>(
        `/api/v1/admin/conversations/${id}`,
      );
      return { data: { ...result, id: result.conversation_id } as unknown as TData };
    }
    if (resource === "knowledge-documents") {
      return { data: await adminRequest<TData>(`/api/v1/admin/knowledge/documents/${id}`) };
    }
    if (resource === "rag-runs") {
      return { data: await adminRequest<TData>(`/api/v1/admin/rag-runs/${id}`) };
    }
    return unsupported();
}

async function custom<
  TData extends BaseRecord = BaseRecord,
  TQuery = unknown,
  TPayload = unknown,
>({ url, method, payload, query }: CustomParams<TQuery, TPayload>): Promise<{ data: TData }> {
  const data = await adminRequest<TData>(url, {
    method: method.toUpperCase() as "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
    body: payload,
    query: query as Record<string, unknown> | undefined,
  });
  return { data };
}

export const adminDataProvider: DataProvider = {
  getList,
  getOne,
  async create() {
    return unsupported();
  },
  async update() {
    return unsupported();
  },
  async deleteOne() {
    return unsupported();
  },
  getApiUrl() {
    return adminApiUrl;
  },
  custom,
};

type Subscription = {
  channel: string;
  conversationIds: Set<string>;
  callback: (event: LiveEvent) => void;
};

const subscriptions = new Set<Subscription>();
let eventSource: EventSource | undefined;

export function mapAdminEvent(eventType: string, rawData: string): AdminStreamEvent | null {
  if (eventType === "keepalive") return null;
  try {
    return JSON.parse(rawData) as AdminStreamEvent;
  } catch {
    return null;
  }
}

function openEventStream(): void {
  if (eventSource || typeof EventSource === "undefined") return;
  eventSource = new EventSource(`${adminApiUrl}/api/v1/admin/events`, {
    withCredentials: true,
  });
  ADMIN_EVENTS.forEach((eventType) => {
    eventSource?.addEventListener(eventType, (message) => {
      const source = message as MessageEvent<string>;
      const payload = mapAdminEvent(eventType, source.data);
      if (!payload) return;
      subscriptions.forEach((subscription) => {
        if (
          subscription.conversationIds.size > 0 &&
          !subscription.conversationIds.has(payload.conversation_id)
        ) {
          return;
        }
        subscription.callback({
          channel: subscription.channel,
          type: "updated",
          payload: { ids: [payload.conversation_id], eventType, ...payload },
          date: new Date(payload.timestamp),
        });
      });
    });
  });
}

export const adminLiveProvider: LiveProvider = {
  subscribe({ channel, callback, params }) {
    const ids = [...(params?.ids ?? []), ...(params?.id ? [params.id] : [])];
    const subscription: Subscription = {
      channel,
      callback,
      conversationIds: new Set(ids.map(String)),
    };
    subscriptions.add(subscription);
    openEventStream();
    return subscription;
  },
  unsubscribe(subscription: Subscription) {
    subscriptions.delete(subscription);
    if (subscriptions.size === 0) {
      eventSource?.close();
      eventSource = undefined;
    }
  },
};
