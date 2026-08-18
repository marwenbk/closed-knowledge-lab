import { afterEach, describe, expect, it, vi } from "vitest";

import {
  adminDataProvider,
  adminRequest,
  mapAdminEvent,
} from "@/lib/admin-client";

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "topmed_admin_csrf=; Max-Age=0; path=/";
});

describe("admin client", () => {
  it("sends session credentials and the double-submit token on mutations", async () => {
    document.cookie = "topmed_admin_csrf=csrf-token; path=/";
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ state: "HUMAN_ASSIGNED" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await adminRequest("/api/v1/admin/conversations/id/claim", {
      method: "POST",
      body: {},
    });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.credentials).toBe("include");
    expect((init.headers as Headers).get("X-CSRF-Token")).toBe("csrf-token");
  });

  it("never forwards administrator cookies to another origin", async () => {
    await expect(adminRequest("https://untrusted.example/api")).rejects.toMatchObject({
      code: "INVALID_ADMIN_URL",
    });
  });

  it("maps the handoff queue to Refine records and server filters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          items: [
            {
              conversation_id: "conversation-id",
              state: "HUMAN_REQUESTED",
              priority: "HIGH",
              reason: "CONFLICTING_EVIDENCE",
              requested_at: "2026-08-18T12:00:00Z",
              assigned_agent_id: null,
              claimed_at: null,
              waiting_seconds: 10,
              latest_customer_message: "Preciso de ajuda",
              assigned_agent_name: null,
              answerability_status: "CONFLICTING_EVIDENCE",
            },
          ],
          total: 1,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await adminDataProvider.getList({
      resource: "handoffs",
      pagination: { currentPage: 1, pageSize: 25 },
      filters: [{ field: "state", operator: "eq", value: "HUMAN_REQUESTED" }],
    });

    expect(result.data[0]?.id).toBe("conversation-id");
    expect(fetchMock.mock.calls[0]?.[0]).toContain("state=HUMAN_REQUESTED");
    expect(fetchMock.mock.calls[0]?.[0]).toContain("limit=25");
  });

  it("parses operational events and ignores protocol events", () => {
    expect(
      mapAdminEvent(
        "handoff.requested",
        JSON.stringify({
          event_id: 9,
          conversation_id: "conversation-id",
          timestamp: "2026-08-18T12:00:00Z",
          visibility: "INTERNAL",
          payload: {},
        }),
      ),
    ).toMatchObject({ event_id: 9, conversation_id: "conversation-id" });
    expect(mapAdminEvent("keepalive", "{}")).toBeNull();
    expect(mapAdminEvent("handoff.requested", "invalid")).toBeNull();
  });
});
