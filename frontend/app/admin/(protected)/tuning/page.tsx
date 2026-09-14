"use client";

import { useCustomMutation, useGetIdentity, useInvalidate, useList } from "@refinedev/core";
import { FlaskConical, Plus } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";

import { ErrorState, formatDate, LoadingState, PageHeading, Panel, StatusBadge } from "@/components/admin-ui";
import { Button } from "@/components/ui/button";
import type {
  AdminIdentity,
  EvaluationSummary,
  PromptVersion,
  SettingsVersion,
} from "@/lib/admin-types";

const EDITOR_ROLES = new Set(["ADMIN", "SUPERVISOR", "KNOWLEDGE_EDITOR"]);

function Versions<T extends PromptVersion | SettingsVersion>({
  title,
  resource,
  path,
  versions,
}: {
  title: string;
  resource: string;
  path: string;
  versions: T[];
}) {
  return (
    <Panel>
      <h2 className="text-lg font-semibold">{title}</h2>
      <div className="mt-3 grid gap-2">
        {versions.map((version) => (
          <Link
            className="rounded-xl border border-slate-200 p-3 transition hover:border-teal-300"
            href={`/admin/tuning/${path}/${version.id}`}
            key={`${resource}-${version.id}`}
          >
            <div className="flex items-center justify-between gap-3">
              <strong>{version.version}</strong>
              <StatusBadge value={version.status} />
            </div>
            <p className="mt-1 text-xs text-slate-500">
              {version.content_checksum.slice(0, 12)} · {formatDate(version.updated_at)}
            </p>
          </Link>
        ))}
      </div>
    </Panel>
  );
}

export default function TuningPage() {
  const [promptVersion, setPromptVersion] = useState("");
  const [settingsVersion, setSettingsVersion] = useState("");
  const identity = useGetIdentity<AdminIdentity>();
  const prompts = useList<PromptVersion>({ resource: "prompt-versions", pagination: { mode: "off" } });
  const settings = useList<SettingsVersion>({ resource: "settings-versions", pagination: { mode: "off" } });
  const evaluations = useList<EvaluationSummary>({
    resource: "evaluation-runs",
    pagination: { currentPage: 1, pageSize: 5 },
  });
  const mutation = useCustomMutation();
  const invalidate = useInvalidate();
  const canEdit = identity.data?.roles.some((role) => EDITOR_ROLES.has(role)) ?? false;

  async function create(
    event: FormEvent<HTMLFormElement>,
    kind: "prompts" | "settings",
    version: string,
  ) {
    event.preventDefault();
    if (!version.trim()) return;
    await mutation.mutateAsync({
      url: `/api/v1/admin/${kind}`,
      method: "post",
      values: { version: version.trim() },
      successNotification: { message: `Draft ${version.trim()} created.`, type: "success" },
      errorNotification: (error) => ({ message: "The version could not be created.", description: error?.message, type: "error" }),
    });
    if (kind === "prompts") setPromptVersion("");
    else setSettingsVersion("");
    await invalidate({ resource: `${kind === "prompts" ? "prompt" : "settings"}-versions`, invalidates: ["list"] });
  }

  const loading = prompts.query.isLoading || settings.query.isLoading;
  const error = prompts.query.error ?? settings.query.error;
  return (
    <>
      <PageHeading
        title="Tuning versionados"
        description="Prompts and retrieval reach production only after comparable evaluation and authorized activation."
      />
      {canEdit ? (
        <div className="mb-5 grid gap-4 md:grid-cols-2">
          {([
            ["prompts", "New prompt", promptVersion, setPromptVersion],
            ["settings", "New settings", settingsVersion, setSettingsVersion],
          ] as const).map(([kind, label, value, setter]) => (
            <Panel key={kind}>
              <form className="flex items-end gap-3" onSubmit={(event) => void create(event, kind, value)}>
                <label className="grid flex-1 gap-1 text-sm font-medium">
                  {label}
                  <input
                    className="rounded-xl border border-slate-300 px-3 py-2"
                    onChange={(event) => setter(event.target.value)}
                    pattern="[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?"
                    placeholder="1.0.1"
                    required
                    value={value}
                  />
                </label>
                <Button disabled={mutation.mutation.isPending} type="submit"><Plus size={16} /> Criar</Button>
              </form>
            </Panel>
          ))}
        </div>
      ) : null}
      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} /> : null}
      <div className="grid gap-4 lg:grid-cols-2">
        <Versions title="Prompts" resource="prompt" path="prompts" versions={prompts.result.data} />
        <Versions title="Retrieval" resource="settings" path="settings" versions={settings.result.data} />
      </div>
      <Panel className="mt-4">
        <h2 className="flex items-center gap-2 text-lg font-semibold"><FlaskConical size={18} /> Recent evaluations</h2>
        <div className="mt-3 grid gap-2">
          {evaluations.result.data.map((run) => (
            <Link className="flex items-center justify-between rounded-xl border border-slate-200 p-3" href={`/admin/tuning/evaluations/${run.id}`} key={run.id}>
              <span><strong>{run.suite}</strong><small className="ml-2 text-slate-500">{run.mode}</small></span>
              <StatusBadge value={run.status} />
            </Link>
          ))}
        </div>
      </Panel>
    </>
  );
}
