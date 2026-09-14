"use client";

import { HeartPulse, X } from "lucide-react";
import { useEffect } from "react";

import { ChatThread, ConnectionDot, ConversationActions } from "@/components/chat-thread";
import { WidgetBridge } from "@/components/widget-bridge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { WidgetRuntimeProvider } from "@/providers/widget-runtime";

type ChatExperienceProps = {
  apiBaseUrl: string;
  assistantKey: string;
  locale?: string;
  mode: "page" | "widget";
  parentOrigin?: string;
};

function ChatFrame({ mode, parentOrigin }: Pick<ChatExperienceProps, "mode" | "parentOrigin">) {
  const requestClose = () => {
    if (!parentOrigin || window.parent === window) return;
    try {
      window.parent.postMessage({ type: "topmed.widget.close" }, new URL(parentOrigin).origin);
    } catch {
      // An invalid host origin disables bridge messaging without breaking chat.
    }
  };

  useEffect(() => {
    if (mode === "widget") document.body.classList.add("widget-body");
    return () => document.body.classList.remove("widget-body");
  }, [mode]);

  return (
    <main
      className={cn(
        "flex min-h-0 flex-col overflow-hidden border-slate-200 bg-white",
        mode === "widget"
          ? "h-dvh w-full"
          : "h-[min(820px,calc(100dvh-3rem))] w-full max-w-5xl rounded-[2rem] border shadow-2xl shadow-slate-950/10",
      )}
    >
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-teal-700 text-white">
            <HeartPulse className="size-4.5" />
          </div>
          <div className="min-w-0">
            <h1 className="truncate text-sm font-bold text-slate-950">Closed-Knowledge Lab</h1>
            <ConnectionDot />
          </div>
        </div>
        <div className="flex items-center gap-1">
          <ConversationActions />
          {mode === "widget" ? (
            <Button aria-label="Close chat" onClick={requestClose} size="icon" variant="ghost">
              <X className="size-4" />
            </Button>
          ) : null}
        </div>
      </header>
      <ChatThread />
      {mode === "widget" ? <WidgetBridge parentOrigin={parentOrigin} /> : null}
    </main>
  );
}

export function ChatExperience(props: ChatExperienceProps) {
  return (
    <WidgetRuntimeProvider
      apiBaseUrl={props.apiBaseUrl}
      assistantKey={props.assistantKey}
      locale={props.locale}
    >
      <ChatFrame mode={props.mode} parentOrigin={props.parentOrigin} />
    </WidgetRuntimeProvider>
  );
}
