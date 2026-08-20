"use client";

import { Authenticated, Refine, type NotificationProvider } from "@refinedev/core";
import routerProvider from "@refinedev/nextjs-router";
import { useMemo, useState, type ReactNode } from "react";

import {
  adminAccessControlProvider,
  adminAuthProvider,
  adminDataProvider,
  adminLiveProvider,
} from "@/lib/admin-client";

type Notice = { key: string; message: string; description?: string; type: string };

export function AdminProvider({ children }: { children: ReactNode }) {
  const [notices, setNotices] = useState<Notice[]>([]);
  const notificationProvider = useMemo<NotificationProvider>(
    () => ({
      open(notice) {
        const key = notice.key ?? crypto.randomUUID();
        setNotices((current) => [
          ...current.filter((item) => item.key !== key),
          { ...notice, key },
        ]);
        window.setTimeout(
          () => setNotices((current) => current.filter((item) => item.key !== key)),
          5000,
        );
      },
      close(key) {
        setNotices((current) => current.filter((item) => item.key !== key));
      },
    }),
    [],
  );

  return (
    <Refine
      routerProvider={routerProvider}
      dataProvider={adminDataProvider}
      authProvider={adminAuthProvider}
      accessControlProvider={adminAccessControlProvider}
      liveProvider={adminLiveProvider}
      notificationProvider={notificationProvider}
      resources={[
        { name: "dashboard", list: "/admin" },
        { name: "handoffs", list: "/admin/handoffs" },
        { name: "conversations", show: "/admin/conversations/:id" },
        {
          name: "knowledge-versions",
          list: "/admin/knowledge",
          show: "/admin/knowledge/:id",
        },
        { name: "prompt-versions", list: "/admin/tuning", show: "/admin/tuning/prompts/:id" },
        { name: "settings-versions", list: "/admin/tuning", show: "/admin/tuning/settings/:id" },
        { name: "evaluation-runs", show: "/admin/tuning/evaluations/:id" },
        { name: "audit-events", list: "/admin/audit" },
        { name: "feedback", list: "/admin/audit" },
        { name: "rag-runs" },
      ]}
      options={{ disableTelemetry: true, liveMode: "auto", syncWithLocation: true }}
    >
      {children}
      <div className="fixed right-4 top-4 z-50 grid w-[min(24rem,calc(100%-2rem))] gap-2">
        {notices.map((notice) => (
          <button
            className={`rounded-xl border p-4 text-left shadow-lg ${
              notice.type === "error"
                ? "border-red-200 bg-red-50 text-red-900"
                : "border-teal-200 bg-white text-slate-900"
            }`}
            key={notice.key}
            onClick={() => notificationProvider.close(notice.key)}
            type="button"
          >
            <span className="block font-semibold">{notice.message}</span>
            {notice.description ? (
              <span className="mt-1 block text-sm opacity-80">{notice.description}</span>
            ) : null}
          </button>
        ))}
      </div>
    </Refine>
  );
}

export function AdminGuard({ children }: { children: ReactNode }) {
  return (
    <Authenticated
      key="admin-protected"
      redirectOnFail="/admin/login"
      loading={<main className="grid min-h-screen place-items-center">A validar sessão…</main>}
    >
      {children}
    </Authenticated>
  );
}
