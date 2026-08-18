import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createWidgetSession,
  normalizeApiBaseUrl,
  requestHumanSupport,
  WidgetApiError,
} from "@/lib/widget-api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("normalizeApiBaseUrl", () => {
  it("normalizes an HTTP API root", () => {
    expect(normalizeApiBaseUrl("http://127.0.0.1:8000/")).toBe("http://127.0.0.1:8000");
  });

  it.each(["javascript:alert(1)", "https://user:secret@example.com"])(
    "rejects an unsafe URL: %s",
    (value) => {
      expect(() => normalizeApiBaseUrl(value)).toThrow("URL da API");
    },
  );
});

describe("widget API", () => {
  it("creates a session without exposing credentials in the URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          session_id: "db663d73-fb6e-4f42-8792-d31f0648cbd1",
          token: "signed-widget-token",
          token_type: "Bearer",
          expires_at: "2026-08-18T12:00:00Z",
          locale: "pt-BR",
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const session = await createWidgetSession(
      "http://127.0.0.1:8000",
      "topmed-local-demo",
      "pt-BR",
    );

    expect(session.token).toBe("signed-widget-token");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/v1/widget/sessions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          assistant_key: "topmed-local-demo",
          locale: "pt-BR",
        }),
      }),
    );
  });

  it("preserves the backend's stable error code and request ID", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: "INVALID_ASSISTANT_KEY",
              message: "invalid",
              request_id: "5d2422d8-a499-4b1f-8f6e-860483e38095",
            },
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(
      createWidgetSession("http://127.0.0.1:8000", "wrong", "pt-BR"),
    ).rejects.toEqual(
      new WidgetApiError(
        401,
        "INVALID_ASSISTANT_KEY",
        "invalid",
        "5d2422d8-a499-4b1f-8f6e-860483e38095",
      ),
    );
  });

  it("requests human support with the scoped widget bearer token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          conversation_id: "0b7900b3-f3bb-49ce-a2a1-3424460aa411",
          state: "HUMAN_REQUESTED",
          priority: "NORMAL",
          reason: "CUSTOMER_REQUEST",
          requested_at: "2026-08-18T12:00:00Z",
          assigned_agent_id: null,
          claimed_at: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await requestHumanSupport(
      "http://127.0.0.1:8000",
      "signed-widget-token",
      "0b7900b3-f3bb-49ce-a2a1-3424460aa411",
    );

    expect(result.state).toBe("HUMAN_REQUESTED");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/v1/widget/conversations/0b7900b3-f3bb-49ce-a2a1-3424460aa411/request-human",
      expect.objectContaining({
        method: "POST",
        headers: expect.any(Headers),
      }),
    );
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect((request.headers as Headers).get("Authorization")).toBe("Bearer signed-widget-token");
  });
});
