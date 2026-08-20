import { afterEach, describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import { proxy } from "@/proxy";

afterEach(() => {
  delete process.env.TOPMED_PUBLIC_ORIGIN;
});

describe("production API proxy", () => {
  it("adds the canonical origin when a same-origin GET omits it", () => {
    process.env.TOPMED_PUBLIC_ORIGIN = "https://topmed.example";

    const response = proxy(new NextRequest("https://topmed.example/api/v1/widget/events"));

    expect(response.headers.get("x-middleware-request-origin")).toBe(
      "https://topmed.example",
    );
  });

  it("preserves a browser-supplied origin", () => {
    process.env.TOPMED_PUBLIC_ORIGIN = "https://topmed.example";
    const request = new NextRequest("https://topmed.example/api/v1/widget/sessions", {
      headers: { Origin: "https://external.example" },
    });

    const response = proxy(request);

    expect(response.headers.get("x-middleware-request-origin")).toBe(
      "https://external.example",
    );
  });
});
