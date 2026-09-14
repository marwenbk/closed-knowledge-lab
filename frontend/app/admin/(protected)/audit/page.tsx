"use client";

import { useList } from "@refinedev/core";
import Link from "next/link";
import { useState } from "react";

import { ErrorState, formatDate, LoadingState, PageHeading, Panel, StatusBadge } from "@/components/admin-ui";
import type { AuditEvent, Feedback, FeedbackCategory } from "@/lib/admin-types";

const categories: FeedbackCategory[] = [
  "CORRECT",
  "INCORRECT",
  "MISSING_KB_INFORMATION",
  "CONFLICTING_KB_INFORMATION",
  "RETRIEVAL_FAILURE",
  "GROUNDING_FAILURE",
  "ESCALATION_APPROPRIATE",
  "ESCALATION_UNNECESSARY",
];

export default function AuditPage() {
  const [eventType, setEventType] = useState("");
  const [actorType, setActorType] = useState("");
  const [resourceType, setResourceType] = useState("");
  const [category, setCategory] = useState("");
  const events = useList<AuditEvent>({
    resource: "audit-events",
    pagination: { currentPage: 1, pageSize: 50 },
    filters: [
      { field: "event_type", operator: "eq", value: eventType },
      { field: "actor_type", operator: "eq", value: actorType },
      { field: "resource_type", operator: "eq", value: resourceType },
    ],
  });
  const feedback = useList<Feedback>({
    resource: "feedback",
    pagination: { currentPage: 1, pageSize: 50 },
    filters: [{ field: "category", operator: "eq", value: category }],
  });
  return (
    <>
      <PageHeading
        title="Audit e feedback"
        description="Append-only history of operational decisions and human evaluations, without secrets or internal model reasoning."
      />
      <div className="grid gap-5 xl:grid-cols-[1.35fr_.65fr]">
        <Panel>
          <h2 className="text-lg font-semibold">Audit events</h2>
          <div className="mt-4 grid gap-2 sm:grid-cols-3">
            {[
              ["Event type", eventType, setEventType],
              ["Actor type", actorType, setActorType],
              ["Resource type", resourceType, setResourceType],
            ].map(([label, value, setter]) => (
              <label className="grid gap-1 text-xs font-medium" key={label as string}>
                {label as string}
                <input className="rounded-lg border border-slate-300 px-3 py-2 text-sm" onChange={(event) => (setter as (value: string) => void)(event.target.value)} value={value as string} />
              </label>
            ))}
          </div>
          {events.query.isLoading ? <LoadingState /> : null}
          {events.query.error ? <ErrorState error={events.query.error} /> : null}
          <div className="mt-4 grid gap-2">
            {events.result.data.map((event) => (
              <details className="rounded-xl border border-slate-200" key={event.id}>
                <summary className="cursor-pointer p-3 text-sm">
                  <span className="font-semibold">{event.event_type}</span>
                  <span className="ml-2 text-xs text-slate-500">{event.actor_type} · {event.resource_type} · {formatDate(event.created_at)}</span>
                </summary>
                <pre className="max-h-80 overflow-auto border-t border-slate-200 bg-slate-950 p-3 text-xs text-slate-100">{JSON.stringify(event, null, 2)}</pre>
              </details>
            ))}
          </div>
        </Panel>
        <Panel>
          <h2 className="text-lg font-semibold">Feedback estruturado</h2>
          <label className="mt-4 grid gap-1 text-xs font-medium">
            Categoria
            <select className="rounded-lg border border-slate-300 px-3 py-2 text-sm" onChange={(event) => setCategory(event.target.value)} value={category}>
              <option value="">All</option>
              {categories.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          {feedback.query.isLoading ? <LoadingState /> : null}
          {feedback.query.error ? <ErrorState error={feedback.query.error} /> : null}
          <div className="mt-4 grid gap-3">
            {feedback.result.data.map((item) => (
              <article className="rounded-xl border border-slate-200 p-3 text-sm" key={item.id}>
                <div className="flex flex-wrap items-center justify-between gap-2"><StatusBadge value={item.category} /><span className="text-xs text-slate-500">{formatDate(item.created_at)}</span></div>
                {item.note ? <p className="mt-2 text-slate-700">{item.note}</p> : null}
                <Link className="mt-2 inline-block text-xs font-semibold text-teal-700" href={`/admin/conversations/${item.conversation_id}`}>Open conversation</Link>
              </article>
            ))}
          </div>
        </Panel>
      </div>
    </>
  );
}
