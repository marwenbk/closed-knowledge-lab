"use client";

import { useOne } from "@refinedev/core";
import { ArrowLeft, FileText } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import {
  ErrorState,
  LoadingState,
  PageHeading,
  Panel,
  StatusBadge,
} from "@/components/admin-ui";
import type { KnowledgeDocumentDetail } from "@/lib/admin-types";

export default function KnowledgeDocumentPage() {
  const { id } = useParams<{ id: string }>();
  const { result: document, query } = useOne<KnowledgeDocumentDetail>({
    resource: "knowledge-documents",
    id,
  });

  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ErrorState error={query.error} />;
  if (!document) return null;

  return (
    <>
      <PageHeading
        title={document.title}
        description={`${document.document_key} · ${document.source_path}`}
        actions={
          <Link
            className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm"
            href="/admin/knowledge"
          >
            <ArrowLeft size={16} /> Voltar
          </Link>
        }
      />
      <Panel className="mb-5">
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge value={document.status} />
          <span className="text-sm text-slate-600">Dataset {document.dataset_version}</span>
          <span className="text-sm text-slate-600">
            Revisão {document.revision?.revision_number ?? "—"}
          </span>
          <span className="font-mono text-xs text-slate-400">
            SHA-256 {document.checksum.slice(0, 16)}…
          </span>
        </div>
      </Panel>
      <div className="grid gap-4">
        {document.chunks.map((chunk) => (
          <Panel key={chunk.id}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2">
                <FileText className="text-teal-700" size={17} />
                <h2 className="font-semibold">{chunk.section}</h2>
              </div>
              <span className="font-mono text-xs text-slate-500">
                #{chunk.ordinal} · {chunk.token_count} tokens
              </span>
            </div>
            <p className="mb-3 text-xs text-slate-500">{chunk.section_path.join(" › ")}</p>
            <div className="whitespace-pre-wrap text-sm leading-7 text-slate-700">
              {chunk.content}
            </div>
            <p className="mt-4 truncate font-mono text-[11px] text-slate-400">
              {chunk.stable_chunk_key}
            </p>
          </Panel>
        ))}
      </div>
    </>
  );
}
