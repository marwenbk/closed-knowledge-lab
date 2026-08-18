"use client";

import {
  useCustomMutation,
  useGetIdentity,
  useInvalidate,
  useList,
} from "@refinedev/core";
import { GitBranch, Plus } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";

import { ErrorState, formatDate, LoadingState, PageHeading, Panel, StatusBadge } from "@/components/admin-ui";
import { Button } from "@/components/ui/button";
import type { AdminIdentity, KnowledgeVersion } from "@/lib/admin-types";

const EDITOR_ROLES = new Set(["ADMIN", "SUPERVISOR", "KNOWLEDGE_EDITOR"]);

export default function KnowledgePage() {
  const [datasetVersion, setDatasetVersion] = useState("");
  const identity = useGetIdentity<AdminIdentity>();
  const { result, query } = useList<KnowledgeVersion>({
    resource: "knowledge-versions",
    pagination: { mode: "off" },
  });
  const action = useCustomMutation();
  const invalidate = useInvalidate();
  const canEdit = identity.data?.roles.some((role) => EDITOR_ROLES.has(role)) ?? false;

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const version = datasetVersion.trim();
    if (!version) return;
    await action.mutateAsync({
      url: "/api/v1/admin/knowledge/versions",
      method: "post",
      values: { dataset_version: version },
      successNotification: { message: `Rascunho ${version} criado.`, type: "success" },
      errorNotification: (error) => ({
        message: "Não foi possível criar o rascunho.",
        description: error?.message,
        type: "error",
      }),
    });
    setDatasetVersion("");
    await invalidate({ resource: "knowledge-versions", invalidates: ["list"] });
  }

  return (
    <>
      <PageHeading
        title="Base de conhecimento"
        description="Versões imutáveis, rascunhos editoriais e os gates exigidos antes da publicação."
      />
      {canEdit ? (
        <Panel className="mb-5">
          <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => void create(event)}>
            <label className="grid min-w-56 flex-1 gap-1 text-sm font-medium">
              Nova versão
              <input
                className="rounded-xl border border-slate-300 px-3 py-2 outline-none focus:border-teal-600 focus:ring-2 focus:ring-teal-100"
                onChange={(event) => setDatasetVersion(event.target.value)}
                pattern="[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?"
                placeholder="2.0.1"
                required
                value={datasetVersion}
              />
            </label>
            <Button disabled={action.mutation.isPending} type="submit">
              <Plus size={16} /> Criar a partir da ativa
            </Button>
          </form>
        </Panel>
      ) : null}

      {query.isLoading ? <LoadingState /> : null}
      {query.error ? <ErrorState error={query.error} /> : null}
      <div className="grid gap-3 md:grid-cols-2">
        {result.data.map((version) => (
          <Link href={`/admin/knowledge/${version.id}`} key={version.id}>
            <Panel className="h-full transition hover:border-teal-300 hover:shadow-md">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="flex items-center gap-2 text-lg font-semibold">
                    <GitBranch className="text-teal-700" size={18} />
                    {version.dataset_version}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    Criada em {formatDate(version.created_at)}
                  </p>
                </div>
                <StatusBadge value={version.status} />
              </div>
              <div className="mt-4 grid grid-cols-3 gap-2 text-center text-xs">
                <span className="rounded-lg bg-slate-50 p-2">
                  <strong className="block text-base">{version.document_count}</strong> documentos
                </span>
                <span className="rounded-lg bg-slate-50 p-2">
                  <strong className="block text-base">{version.chunk_count}</strong> blocos
                </span>
                <span className="rounded-lg bg-slate-50 p-2">
                  <strong className="block text-base">{version.embedded_chunk_count}</strong> indexados
                </span>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <StatusBadge
                  value={version.validation ? (version.validation.passed ? "VALIDATED" : "INVALID") : "NOT_VALIDATED"}
                />
                <StatusBadge value={version.evaluation?.status ?? "NOT_EVALUATED"} />
              </div>
            </Panel>
          </Link>
        ))}
      </div>
    </>
  );
}
