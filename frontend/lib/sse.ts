import type { ConversationEvent } from "@/lib/widget-types";
import { WidgetApiError } from "@/lib/widget-api";

export type ServerEvent = {
  id?: string;
  event: string;
  data: string;
};

function parseBlock(block: string): ServerEvent | null {
  let id: string | undefined;
  let event = "message";
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    const value = separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");
    if (field === "id") id = value;
    if (field === "event") event = value;
    if (field === "data") data.push(value);
  }
  if (!id && data.length === 0 && event === "message") return null;
  return { id, event, data: data.join("\n") };
}

export async function* parseEventStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<ServerEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary = /\r?\n\r?\n/.exec(buffer);
      while (boundary?.index !== undefined) {
        const event = parseBlock(buffer.slice(0, boundary.index));
        buffer = buffer.slice(boundary.index + boundary[0].length);
        if (event) yield event;
        boundary = /\r?\n\r?\n/.exec(buffer);
      }
      if (done) break;
    }
    if (buffer.trim()) {
      const event = parseBlock(buffer);
      if (event) yield event;
    }
  } finally {
    reader.releaseLock();
  }
}

export async function subscribeToConversation(
  apiBaseUrl: string,
  token: string,
  conversationId: string,
  lastEventId: number,
  signal: AbortSignal,
  onEvent: (type: string, event: ConversationEvent) => void,
): Promise<void> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/widget/conversations/${conversationId}/events`,
    {
      headers: {
        Accept: "text/event-stream",
        Authorization: `Bearer ${token}`,
        "Last-Event-ID": String(lastEventId),
      },
      cache: "no-store",
      signal,
    },
  );
  if (!response.ok || !response.body) {
    throw new WidgetApiError(
      response.status,
      "EVENT_STREAM_UNAVAILABLE",
      "Realtime updates are unavailable.",
    );
  }
  for await (const item of parseEventStream(response.body)) {
    if (item.event === "keepalive") continue;
    if (item.event === "error") {
      throw new WidgetApiError(
        503,
        "EVENT_STREAM_UNAVAILABLE",
        "Realtime updates are unavailable.",
      );
    }
    const payload = JSON.parse(item.data) as ConversationEvent;
    if (!Number.isSafeInteger(payload.event_id) || payload.event_id < 0) {
      throw new WidgetApiError(
        502,
        "INVALID_EVENT_STREAM",
        "Realtime updates returned an invalid event.",
      );
    }
    onEvent(item.event, payload);
  }
}
