"use client";

import { useList } from "@refinedev/core";
import { Search } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";

import { ErrorState, LoadingState, PageHeading, Panel, StatusBadge } from "@/components/admin-ui";
import { Button } from "@/components/ui/button";
import type { KnowledgeDocument } from "@/lib/admin-types";

export default function KnowledgePage() {
  const [search, setSearch] = useState("");
  const [queryText, setQueryText] = useState("");
  const { result, query } = useList<KnowledgeDocument>({
    resource: "knowledge-documents",
    pagination: { currentPage: 1, pageSize: 100 },
    filters: [{ field: "query", operator: "contains", value: queryText || undefined }],
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQueryText(search.trim());
  }

  return (
    <>
      <PageHeading
        title="Base de conhecimento"
        description="Projeção imutável dos documentos Markdown ativos e dos blocos usados na recuperação."
      />
      <Panel className="mb-5">
        <form className="flex gap-2" onSubmit={submit}>
          <label className="relative flex-1">
            <span className="sr-only">Pesquisar documentos</span>
            <Search className="absolute left-3 top-2.5 text-slate-400" size={18} />
            <input
              className="w-full rounded-xl border border-slate-300 py-2 pl-10 pr-3 text-sm outline-none focus:border-teal-600 focus:ring-2 focus:ring-teal-100"
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Título, chave ou caminho…"
              value={search}
            />
          </label>
          <Button type="submit">
            Pesquisar
          </Button>
        </form>
      </Panel>

      {query.isLoading ? <LoadingState /> : null}
      {query.error ? <ErrorState error={query.error} /> : null}
      <div className="grid gap-3 md:grid-cols-2">
        {result.data.map((document) => (
          <Link href={`/admin/knowledge/${document.id}`} key={document.id}>
            <Panel className="h-full transition hover:border-teal-300 hover:shadow-md">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="font-semibold">{document.title}</h2>
                  <p className="mt-1 truncate font-mono text-xs text-slate-500">
                    {document.document_key}
                  </p>
                </div>
                <StatusBadge value={document.status} />
              </div>
              <p className="mt-4 truncate text-xs text-slate-500">{document.source_path}</p>
              <p className="mt-2 text-sm font-medium text-teal-700">
                {document.chunk_count} blocos
              </p>
            </Panel>
          </Link>
        ))}
      </div>
      {!query.isLoading && !query.error && result.data.length === 0 ? (
        <Panel>
          <p className="py-8 text-center text-sm text-slate-500">Nenhum documento encontrado.</p>
        </Panel>
      ) : null}
    </>
  );
}
