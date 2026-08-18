"use client";

import { useAuiState } from "@assistant-ui/react";
import { useEffect, useRef, useState } from "react";

import { useWidgetRuntime } from "@/providers/widget-runtime";

const INCOMING_TYPES = new Set(["topmed.widget.visibility", "topmed.widget.theme"]);

function exactOrigin(value?: string): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.origin : null;
  } catch {
    return null;
  }
}

export function WidgetBridge({ parentOrigin }: { parentOrigin?: string }) {
  const messages = useAuiState((state) => state.thread.messages);
  const { isReady } = useWidgetRuntime();
  const [visible, setVisible] = useState(false);
  const assistantCount = messages.filter((message) => message.role === "assistant").length;
  const previousAssistantCount = useRef(assistantCount);
  const initialized = useRef(false);
  const normalizedOrigin = exactOrigin(parentOrigin);

  useEffect(() => {
    if (!normalizedOrigin || window.parent === window) return;
    const post = (type: string, payload: Record<string, unknown> = {}) => {
      window.parent.postMessage({ type, ...payload }, normalizedOrigin);
    };
    const onMessage = (event: MessageEvent) => {
      if (
        event.origin !== normalizedOrigin ||
        event.source !== window.parent ||
        typeof event.data !== "object" ||
        event.data === null ||
        !INCOMING_TYPES.has(event.data.type)
      ) {
        return;
      }
      if (event.data.type === "topmed.widget.visibility") setVisible(event.data.open === true);
      if (event.data.type === "topmed.widget.theme") {
        document.documentElement.dataset.theme = event.data.theme === "dark" ? "dark" : "light";
      }
    };
    window.addEventListener("message", onMessage);
    post("topmed.widget.ready");
    return () => window.removeEventListener("message", onMessage);
  }, [normalizedOrigin]);

  useEffect(() => {
    if (!normalizedOrigin || window.parent === window) return;
    if (!isReady || !initialized.current) {
      initialized.current = isReady;
      previousAssistantCount.current = assistantCount;
      return;
    }
    if (!visible && assistantCount > previousAssistantCount.current) {
      window.parent.postMessage(
        { type: "topmed.widget.unread", count: assistantCount - previousAssistantCount.current },
        normalizedOrigin,
      );
    }
    previousAssistantCount.current = assistantCount;
  }, [assistantCount, isReady, normalizedOrigin, visible]);

  return null;
}
