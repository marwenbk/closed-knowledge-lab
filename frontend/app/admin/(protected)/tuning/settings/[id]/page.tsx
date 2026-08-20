"use client";

import { useCustomMutation, useGetIdentity, useInvalidate, useOne } from "@refinedev/core";
import { ArrowLeft, FlaskConical, RotateCcw, Save, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { ErrorState, formatDate, LoadingState, PageHeading, Panel, StatusBadge } from "@/components/admin-ui";
import { Button } from "@/components/ui/button";
import type { AdminIdentity, RetrievalTuning, SettingsVersion } from "@/lib/admin-types";

const EDITOR_ROLES = new Set(["ADMIN", "SUPERVISOR", "KNOWLEDGE_EDITOR"]);
const PUBLISHER_ROLES = new Set(["ADMIN", "SUPERVISOR"]);
const numericFields: Array<{ key: keyof RetrievalTuning; label: string; step?: number }> = [
  { key: "vector_top_k", label: "Vector top K" },
  { key: "lexical_top_k", label: "Lexical top K" },
  { key: "trigram_top_k", label: "Trigram top K" },
  { key: "final_context_k", label: "Contexto final K" },
  { key: "rrf_k", label: "RRF K" },
  { key: "min_vector_similarity", label: "Similaridade vetorial mínima", step: 0.01 },
  { key: "trigram_min_similarity", label: "Similaridade trigram mínima", step: 0.01 },
];

export default function SettingsVersionPage() {
  const { id } = useParams<{ id: string }>();
  const identity = useGetIdentity<AdminIdentity>();
  const { result: version, query } = useOne<SettingsVersion>({ resource: "settings-versions", id });
  const [values, setValues] = useState<RetrievalTuning | null>(null);
  const mutation = useCustomMutation();
  const invalidate = useInvalidate();
  const canEdit = identity.data?.roles.some((role) => EDITOR_ROLES.has(role)) ?? false;
  const canPublish = identity.data?.roles.some((role) => PUBLISHER_ROLES.has(role)) ?? false;
  async function refresh() {
    await Promise.all([
      invalidate({ resource: "settings-versions", id, invalidates: ["detail"] }),
      invalidate({ resource: "settings-versions", invalidates: ["list"] }),
      invalidate({ resource: "evaluation-runs", invalidates: ["list"] }),
      invalidate({ resource: "dashboard", invalidates: ["all"] }),
    ]);
  }

  async function save() {
    if (!values) return;
    await mutation.mutateAsync({
      url: `/api/v1/admin/settings/${id}`,
      method: "put",
      values,
      successNotification: { message: "Ajustes salvos como nova revisão.", type: "success" },
      errorNotification: (error) => ({ message: "Não foi possível salvar.", description: error?.message, type: "error" }),
    });
    await refresh();
  }

  async function run(action: "EVALUATE" | "ACTIVATE" | "ROLLBACK") {
    if (action !== "EVALUATE" && !window.confirm("Confirmar a troca dos ajustes ativos?")) return;
    await mutation.mutateAsync({
      url: `/api/v1/admin/settings/${id}/actions`,
      method: "post",
      values: { action },
      successNotification: { message: action === "EVALUATE" ? "63 casos locais concluídos." : "Ajustes ativos atualizados.", type: "success" },
      errorNotification: (error) => ({ message: "A ação não foi concluída.", description: error?.message, type: "error" }),
    });
    await refresh();
  }

  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ErrorState error={query.error} />;
  if (!version) return null;
  const editedValues = values ?? version.settings;
  const mutable = ["DRAFT", "EVALUATED"].includes(version.status) && canEdit;
  const busy = mutation.mutation.isPending;
  return (
    <>
      <PageHeading
        title={`Busca ${version.version}`}
        description={`${version.content_checksum.slice(0, 16)}… · atualizado ${formatDate(version.updated_at)}`}
        actions={<Link className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm" href="/admin/tuning"><ArrowLeft size={16} /> Ajustes</Link>}
      />
      <Panel className="mb-4">
        <div className="flex flex-wrap items-center gap-2"><StatusBadge value={version.status} /><StatusBadge value={version.evaluation?.status ?? "NOT_EVALUATED"} />{version.requires_reindex ? <StatusBadge value="REINDEX_REQUIRED" /> : null}</div>
        <div className="mt-4 flex flex-wrap gap-2">
          {mutable ? <Button disabled={busy} onClick={() => void save()} type="button" variant="outline"><Save size={16} /> Salvar revisão</Button> : null}
          {canEdit && version.status !== "ACTIVE" ? <Button disabled={busy} onClick={() => void run("EVALUATE")} type="button" variant="outline"><FlaskConical size={16} /> Avaliar 63 casos</Button> : null}
          {canPublish && version.status === "EVALUATED" ? <Button disabled={busy} onClick={() => void run("ACTIVATE")} type="button"><ShieldCheck size={16} /> Ativar</Button> : null}
          {canPublish && version.status === "RETIRED" ? <Button disabled={busy} onClick={() => void run("ROLLBACK")} type="button"><RotateCcw size={16} /> Rollback</Button> : null}
        </div>
      </Panel>
      <Panel>
        <div className="grid gap-4 md:grid-cols-2">
          {numericFields.map(({ key, label, step }) => (
            <label className="grid gap-1 text-sm font-medium" key={key}>
              {label}
              <input
                className="rounded-xl border border-slate-300 px-3 py-2"
                disabled={!mutable}
                max={key.includes("similarity") ? 1 : key === "rrf_k" ? 10000 : 100}
                min={key.includes("similarity") ? 0 : 1}
                onChange={(event) => setValues({ ...editedValues, [key]: Number(event.target.value) })}
                step={step ?? 1}
                type="number"
                value={String(editedValues[key])}
              />
            </label>
          ))}
          {(["trigram_fallback_enabled", "second_hop_enabled"] as const).map((key) => (
            <label className="flex items-center gap-3 rounded-xl border border-slate-200 p-3 text-sm font-medium" key={key}>
              <input checked={editedValues[key]} disabled={!mutable} onChange={(event) => setValues({ ...editedValues, [key]: event.target.checked })} type="checkbox" />
              {key === "trigram_fallback_enabled" ? "Fallback por trigram" : "Segunda etapa de recuperação"}
            </label>
          ))}
        </div>
      </Panel>
      {version.evaluation ? <Panel className="mt-4"><h2 className="font-semibold">Última avaliação</h2><pre className="mt-3 max-h-96 overflow-auto rounded-xl bg-slate-950 p-4 text-xs text-slate-100">{JSON.stringify(version.evaluation.metrics, null, 2)}</pre></Panel> : null}
    </>
  );
}
