"use client";

import { useCustomMutation, useGetIdentity, useInvalidate, useOne } from "@refinedev/core";
import { ArrowLeft, FlaskConical, RotateCcw, Save, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { ErrorState, formatDate, LoadingState, PageHeading, Panel, StatusBadge } from "@/components/admin-ui";
import { Button } from "@/components/ui/button";
import type { AdminIdentity, PromptBundle, PromptVersion } from "@/lib/admin-types";

const EDITOR_ROLES = new Set(["ADMIN", "SUPERVISOR", "KNOWLEDGE_EDITOR"]);
const PUBLISHER_ROLES = new Set(["ADMIN", "SUPERVISOR"]);
const labels: Record<keyof PromptBundle, string> = {
  answerability_prompt: "Answerability classification",
  generation_prompt: "Grounded generation",
  verification_prompt: "Final verification",
};

export default function PromptVersionPage() {
  const { id } = useParams<{ id: string }>();
  const identity = useGetIdentity<AdminIdentity>();
  const { result: version, query } = useOne<PromptVersion>({ resource: "prompt-versions", id });
  const [prompts, setPrompts] = useState<PromptBundle | null>(null);
  const mutation = useCustomMutation();
  const invalidate = useInvalidate();
  const canEdit = identity.data?.roles.some((role) => EDITOR_ROLES.has(role)) ?? false;
  const canPublish = identity.data?.roles.some((role) => PUBLISHER_ROLES.has(role)) ?? false;

  async function refresh() {
    await Promise.all([
      invalidate({ resource: "prompt-versions", id, invalidates: ["detail"] }),
      invalidate({ resource: "prompt-versions", invalidates: ["list"] }),
      invalidate({ resource: "evaluation-runs", invalidates: ["list"] }),
      invalidate({ resource: "dashboard", invalidates: ["all"] }),
    ]);
  }

  async function save() {
    if (!prompts) return;
    await mutation.mutateAsync({
      url: `/api/v1/admin/prompts/${id}`,
      method: "put",
      values: prompts,
      successNotification: { message: "New prompt revision saved.", type: "success" },
      errorNotification: (error) => ({ message: "Could not save.", description: error?.message, type: "error" }),
    });
    await refresh();
  }

  async function run(action: "EVALUATE" | "ACTIVATE" | "ROLLBACK") {
    if (action === "EVALUATE" && !window.confirm("This evaluation runs 100 DeepSeek cases and may run 100 more to create a baseline. It uses API credit and can take several minutes. Continue?")) return;
    if (action !== "EVALUATE" && !window.confirm("Replace the active prompt? The previous version will remain available for rollback.")) return;
    await mutation.mutateAsync({
      url: `/api/v1/admin/prompts/${id}/actions`,
      method: "post",
      values: { action, confirm_live_cost: action === "EVALUATE" },
      successNotification: { message: action === "EVALUATE" ? "Evaluation completed." : "Active prompt updated.", type: "success" },
      errorNotification: (error) => ({ message: "The action could not be completed.", description: error?.message, type: "error" }),
    });
    await refresh();
  }

  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ErrorState error={query.error} />;
  if (!version) return null;
  const editedPrompts = prompts ?? version.prompts;
  const mutable = ["DRAFT", "EVALUATED"].includes(version.status) && canEdit;
  const busy = mutation.mutation.isPending;
  return (
    <>
      <PageHeading
        title={`Prompt ${version.version}`}
        description={`${version.content_checksum.slice(0, 16)}… · atualizado ${formatDate(version.updated_at)}`}
        actions={<Link className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm" href="/admin/tuning"><ArrowLeft size={16} /> Tuning</Link>}
      />
      <Panel className="mb-4">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge value={version.status} />
          <StatusBadge value={version.evaluation?.status ?? "NOT_EVALUATED"} />
          {version.evaluation?.baseline_run_id ? <span className="text-xs text-slate-500">linked comparison</span> : null}
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {mutable ? <Button disabled={busy} onClick={() => void save()} type="button" variant="outline"><Save size={16} /> Save revision</Button> : null}
          {canPublish && version.status !== "ACTIVE" ? <Button disabled={busy} onClick={() => void run("EVALUATE")} type="button" variant="outline"><FlaskConical size={16} /> {busy ? "Evaluating…" : "Evaluate 100 cases"}</Button> : null}
          {canPublish && version.status === "EVALUATED" ? <Button disabled={busy} onClick={() => void run("ACTIVATE")} type="button"><ShieldCheck size={16} /> Ativar</Button> : null}
          {canPublish && version.status === "RETIRED" ? <Button disabled={busy} onClick={() => void run("ROLLBACK")} type="button"><RotateCcw size={16} /> Rollback</Button> : null}
        </div>
      </Panel>
      <div className="grid gap-4">
        {(Object.keys(labels) as Array<keyof PromptBundle>).map((key) => (
          <Panel key={key}>
            <label className="grid gap-2 text-sm font-semibold">
              {labels[key]}
              <textarea
                className="min-h-48 rounded-xl border border-slate-300 p-3 font-mono text-sm leading-6"
                disabled={!mutable}
                onChange={(event) => setPrompts({ ...editedPrompts, [key]: event.target.value })}
                value={editedPrompts[key]}
              />
            </label>
          </Panel>
        ))}
      </div>
      {version.evaluation ? <Panel className="mt-4"><h2 className="font-semibold">Latest evaluation</h2><pre className="mt-3 max-h-96 overflow-auto rounded-xl bg-slate-950 p-4 text-xs text-slate-100">{JSON.stringify(version.evaluation.metrics, null, 2)}</pre></Panel> : null}
    </>
  );
}
