import { describe, expect, it } from "vitest";

import { parseEventStream } from "@/lib/sse";

function chunks(...values: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      values.forEach((value) => controller.enqueue(encoder.encode(value)));
      controller.close();
    },
  });
}

describe("parseEventStream", () => {
  it("parses named events, replay IDs, comments, and multi-line data", async () => {
    const events = [];
    for await (const event of parseEventStream(
      chunks(
        ": connected\r\nid: 7\r\nevent: message.created\r\ndata: {\"line\":\r\n",
        "data: \"joined\"}\r",
        "\n\r\n",
      ),
    )) {
      events.push(event);
    }

    expect(events).toEqual([
      {
        id: "7",
        event: "message.created",
        data: '{"line":\n"joined"}',
      },
    ]);
  });

  it("emits a final block even when the stream closes without a blank line", async () => {
    const events = [];
    for await (const event of parseEventStream(chunks("event: keepalive\ndata: {}"))) {
      events.push(event);
    }

    expect(events).toEqual([{ event: "keepalive", data: "{}" }]);
  });
});
