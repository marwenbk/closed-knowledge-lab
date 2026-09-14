"use client";

import { useCustomMutation, useInvalidate, useList } from "@refinedev/core";
import Link from "next/link";
import { useState } from "react";

import {
  ErrorState,
  formatDate,
  formatDuration,
  LoadingState,
  PageHeading,
  Panel,
  StatusBadge,
} from "@/components/admin-ui";
import { Button } from "@/components/ui/button";
import type { Handoff } from "@/lib/admin-types";

export default function HandoffQueuePage() {
  const [state, setState] = useState("");
  const [assignment, setAssignment] = useState("all");
  const { result, query } = useList<Handoff>({
    resource: "handoffs",
    pagination: { currentPage: 1, pageSize: 100 },
    filters: [
      { field: "state", operator: "eq", value: state || undefined },
      { field: "assignment", operator: "eq", value: assignment },
    ],
    liveMode: "auto",
  });
  const action = useCustomMutation<Handoff>();
  const invalidate = useInvalidate();

  async function claim(conversationId: string) {
    await action.mutateAsync({
      url: `/api/v1/admin/conversations/${conversationId}/claim`,
      method: "post",
      values: {},
      successNotification: { message: "Handoff assigned.", type: "success" },
      errorNotification: (error) => ({
        message: "The handoff could not be claimed.",
        description: error?.message,
        type: "error",
      }),
    });
    await Promise.all([
      invalidate({ resource: "handoffs", invalidates: ["list"] }),
      invalidate({ resource: "dashboard", invalidates: ["all"] }),
    ]);
  }

  return (
    <>
      <PageHeading
        title="Atendimentos humanos"
        description="Realtime queue for explicit requests and conflicts identified by the assistant."
      />
      <Panel className="mb-5">
        <div className="flex flex-wrap gap-3">
          <label className="grid gap-1 text-xs font-medium text-slate-600">
            Estado
            <select
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
              onChange={(event) => setState(event.target.value)}
              value={state}
            >
              <option value="">All open states</option>
              <option value="HUMAN_REQUESTED">Waiting</option>
              <option value="HUMAN_ASSIGNED">Assigned</option>
              <option value="HUMAN_ACTIVE">In progress</option>
            </select>
          </label>
          <label className="grid gap-1 text-xs font-medium text-slate-600">
            Assignment
            <select
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
              onChange={(event) => setAssignment(event.target.value)}
              value={assignment}
            >
              <option value="all">Toda a equipa</option>
              <option value="unassigned">Unassigned</option>
              <option value="me">Assigneds a mim</option>
            </select>
          </label>
          <Button
            className="ml-auto self-end"
            onClick={() => void query.refetch()}
            size="sm"
            type="button"
            variant="outline"
          >
            Atualizar
          </Button>
        </div>
      </Panel>

      {query.isLoading ? <LoadingState /> : null}
      {query.error ? <ErrorState error={query.error} /> : null}
      {!query.isLoading && !query.error && result.data.length === 0 ? (
        <Panel>
          <p className="py-8 text-center text-sm text-slate-500">No handoffs in this queue.</p>
        </Panel>
      ) : null}
      <div className="grid gap-3">
        {result.data.map((handoff) => (
          <Panel className="grid gap-4 md:grid-cols-[1fr_auto]" key={handoff.id}>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge value={handoff.priority} />
                <StatusBadge value={handoff.state} />
                <span className="text-xs text-slate-500">{handoff.reason}</span>
              </div>
              <p className="mt-3 line-clamp-2 text-sm text-slate-700">
                {handoff.latest_customer_message ?? "No recent message."}
              </p>
              <p className="mt-2 text-xs text-slate-500">
                Espera {formatDuration(handoff.waiting_seconds)} · pedido em {" "}
                {formatDate(handoff.requested_at)}
                {handoff.assigned_agent_name ? ` · ${handoff.assigned_agent_name}` : ""}
              </p>
            </div>
            <div className="flex items-center gap-2 md:justify-end">
              {handoff.state === "HUMAN_REQUESTED" ? (
                <Button
                  disabled={action.mutation.isPending}
                  onClick={() => void claim(handoff.conversation_id)}
                  size="sm"
                  type="button"
                >
                  Assumir
                </Button>
              ) : null}
              <Button asChild size="sm" variant="outline">
                <Link href={`/admin/conversations/${handoff.conversation_id}`}>Open</Link>
              </Button>
            </div>
          </Panel>
        ))}
      </div>
    </>
  );
}
