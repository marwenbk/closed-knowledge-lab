"use client";

import {
  useCustomMutation,
  useGetIdentity,
  useInvalidate,
  useOne,
} from "@refinedev/core";
import { ArrowLeft, CheckCircle2, DatabaseZap, FileText, FlaskConical, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { ErrorState, formatDate, LoadingState, PageHeading, Panel, StatusBadge } from "@/components/admin-ui";
import { Button } from "@/components/ui/button";
import type { AdminIdentity, KnowledgeVersionDetail } from "@/lib/admin-types";

const EDITOR_ROLES = new Set(["ADMIN", "SUPERVISOR", "KNOWLEDGE_EDITOR"]);
const PUBLISHER_ROLES = new Set(["ADMIN", "SUPERVISOR"]);

export default function KnowledgeVersionPage() {
  const { id } = useParams<{ id: string }>();
  const identity = useGetIdentity<AdminIdentity>();
  const { result: version, query } = useOne<KnowledgeVersionDetail>({
    resource: "knowledge-versions",
    id,
  });
  const action = useCustomMutation();
  const invalidate = useInvalidate();
  const canEdit = identity.data?.roles.some((role) => EDITOR_ROLES.has(role)) ?? false;
  const canPublish = identity.data?.roles.some((role) => PUBLISHER_ROLES.has(role)) ?? false;

  async function run(actionName: string, success: string, confirm?: string) {
    if (confirm && !window.confirm(confirm)) return;
    await action.mutateAsync({
      url: `/api/v1/admin/knowledge/versions/${id}/actions`,
      method: "post",
      values: { action: actionName },
      successNotification: { message: success, type: "success" },
      errorNotification: (error) => ({
        message: "O gate não foi concluído.",
        description: error?.message,
        type: "error",
      }),
    });
    await Promise.all([
      invalidate({ resource: "knowledge-versions", id, invalidates: ["detail"] }),
      invalidate({ resource: "knowledge-versions", invalidates: ["list"] }),
      invalidate({ resource: "dashboard", invalidates: ["all"] }),
    ]);
  }

  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ErrorState error={query.error} />;
  if (!version) return null;
  const draft = version.status === "DRAFT";
  const busy = action.mutation.isPending;

  return (
    <>
      <PageHeading
        title={`Conhecimento ${version.dataset_version}`}
        description={`Criada em ${formatDate(version.created_at)} · ${version.manifest_checksum.slice(0, 16)}…`}
        actions={
          <Link className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm" href="/admin/knowledge">
            <ArrowLeft size={16} /> Todas as versões
          </Link>
        }
      />

      <Panel className="mb-5">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge value={version.status} />
          <StatusBadge value={version.validation?.passed ? "VALIDATED" : "NOT_VALIDATED"} />
          <StatusBadge value={version.evaluation?.status ?? "NOT_EVALUATED"} />
          <span className="ml-auto text-xs text-slate-500">
            {version.embedded_chunk_count}/{version.chunk_count} blocos indexados
          </span>
        </div>
        {draft && canEdit ? (
          <div className="mt-5 flex flex-wrap gap-2 border-t border-slate-100 pt-4">
            <Button disabled={busy} onClick={() => void run("VALIDATE", "Validação concluída.")} type="button" variant="outline">
              <CheckCircle2 size={16} /> Validar
            </Button>
            <Button disabled={busy} onClick={() => void run("INDEX", "Indexação concluída.")} type="button" variant="outline">
              <DatabaseZap size={16} /> Indexar
            </Button>
            <Button disabled={busy} onClick={() => void run("EVALUATE", "Avaliação concluída.")} type="button" variant="outline">
              <FlaskConical size={16} /> Avaliar recuperação
            </Button>
            {canPublish ? (
              <Button
                disabled={busy}
                onClick={() => void run("ACTIVATE", "Versão publicada.", "Publicar esta versão e retirar a base atualmente ativa?")}
                type="button"
              >
                Publicar
              </Button>
            ) : null}
          </div>
        ) : null}
        {version.status === "RETIRED" && canPublish ? (
          <div className="mt-5 border-t border-slate-100 pt-4">
            <Button
              disabled={busy}
              onClick={() => void run("ROLLBACK", "Rollback concluído.", "Reativar esta versão e retirar a base atualmente ativa?")}
              type="button"
              variant="outline"
            >
              <RotateCcw size={16} /> Reativar versão
            </Button>
          </div>
        ) : null}
      </Panel>

      {version.validation && !version.validation.passed ? (
        <Panel className="mb-5 border-red-200 bg-red-50">
          <h2 className="font-semibold text-red-900">Erros de validação</h2>
          <ul className="mt-3 grid gap-2 text-sm text-red-800">
            {version.validation.errors.map((error, index) => (
              <li key={`${error.code}-${error.document_key}-${index}`}>
                <strong>{error.code}</strong>{error.document_key ? ` · ${error.document_key}` : ""}: {error.message}
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {version.evaluation ? (
        <Panel className="mb-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-semibold">Última avaliação de publicação</h2>
            <StatusBadge value={version.evaluation.status} />
          </div>
          <p className="mt-2 text-sm text-slate-600">
            {version.evaluation.suite} · {formatDate(version.evaluation.completed_at)}
          </p>
          <details className="mt-3 rounded-xl border border-slate-200">
            <summary className="cursor-pointer px-4 py-3 text-sm font-medium">Métricas verificáveis</summary>
            <pre className="max-h-80 overflow-auto border-t border-slate-200 bg-slate-950 p-4 text-xs text-slate-100">
              {JSON.stringify(version.evaluation.metrics, null, 2)}
            </pre>
          </details>
        </Panel>
      ) : null}

      <div className="grid gap-3 md:grid-cols-2">
        {version.documents.map((document) => (
          <Link href={`/admin/knowledge/${version.id}/documents/${document.id}`} key={document.id}>
            <Panel className="h-full transition hover:border-teal-300 hover:shadow-md">
              <div className="flex items-start gap-3">
                <FileText className="mt-0.5 text-teal-700" size={18} />
                <div className="min-w-0">
                  <h2 className="font-semibold">{document.title}</h2>
                  <p className="mt-1 truncate font-mono text-xs text-slate-500">{document.document_key}</p>
                  <p className="mt-3 text-sm text-teal-700">{document.chunk_count} blocos · abrir revisão</p>
                </div>
              </div>
            </Panel>
          </Link>
        ))}
      </div>
    </>
  );
}
