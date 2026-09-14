"use client";

import { useOne } from "@refinedev/core";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { ErrorState, formatDate, LoadingState, PageHeading, Panel, StatusBadge } from "@/components/admin-ui";
import type { EvaluationDetail } from "@/lib/admin-types";

export default function EvaluationPage() {
  const { id } = useParams<{ id: string }>();
  const { result: run, query } = useOne<EvaluationDetail>({ resource: "evaluation-runs", id });
  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ErrorState error={query.error} />;
  if (!run) return null;
  return (
    <>
      <PageHeading
        title={run.suite}
        description={`${run.mode} · iniciada ${formatDate(run.started_at)}`}
        actions={<Link className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm" href="/admin/tuning"><ArrowLeft size={16} /> Tuning</Link>}
      />
      <Panel>
        <div className="flex flex-wrap items-center gap-2"><StatusBadge value={run.status} />{run.error_code ? <StatusBadge value={run.error_code} /> : null}</div>
        <dl className="mt-4 grid gap-3 text-sm md:grid-cols-2">
          <div><dt className="text-slate-500">Prompt</dt><dd>{run.prompt_version} · {run.prompt_checksum.slice(0, 12)}</dd></div>
          <div><dt className="text-slate-500">Tuning</dt><dd>{run.settings_version} · {run.settings_checksum.slice(0, 12)}</dd></div>
          <div><dt className="text-slate-500">Modelo</dt><dd>{run.model_name}</dd></div>
          <div><dt className="text-slate-500">Embedding</dt><dd>{run.embedding_model} · {run.embedding_version.slice(0, 12)}</dd></div>
        </dl>
        <pre className="mt-5 max-h-[42rem] overflow-auto rounded-xl bg-slate-950 p-4 text-xs text-slate-100">{JSON.stringify(run.metrics, null, 2)}</pre>
      </Panel>
    </>
  );
}
