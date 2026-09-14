"use client";

import { useCustom, useCustomMutation, useGetIdentity, useInvalidate } from "@refinedev/core";
import { ArrowLeft, FileSearch, Save } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";

import { ErrorState, LoadingState, PageHeading, Panel, StatusBadge } from "@/components/admin-ui";
import { Button } from "@/components/ui/button";
import type { AdminIdentity, KnowledgeWorkflowDocument } from "@/lib/admin-types";

const EDITOR_ROLES = new Set(["ADMIN", "SUPERVISOR", "KNOWLEDGE_EDITOR"]);

function markdownBody(value: string): string {
  return value.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, "");
}

export default function KnowledgeDocumentEditorPage() {
  const { id, documentId } = useParams<{ id: string; documentId: string }>();
  const identity = useGetIdentity<AdminIdentity>();
  const { result, query } = useCustom<KnowledgeWorkflowDocument>({
    url: `/api/v1/admin/knowledge/versions/${id}/documents/${documentId}`,
    method: "get",
  });
  const action = useCustomMutation();
  const invalidate = useInvalidate();
  const [edits, setEdits] = useState<Record<string, string>>({});
  const document = result.data;
  const content = edits[document.checksum] ?? document.content_markdown;
  const canEdit =
    document.version_status === "DRAFT" &&
    (identity.data?.roles.some((role) => EDITOR_ROLES.has(role)) ?? false);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await action.mutateAsync({
      url: `/api/v1/admin/knowledge/versions/${id}/documents/${documentId}`,
      method: "put",
      values: { content_markdown: content },
      successNotification: { message: "New revision saved and chunks regenerated.", type: "success" },
      errorNotification: (error) => ({
        message: "The revision was not saved.",
        description: error?.message,
        type: "error",
      }),
    });
    await Promise.all([
      query.refetch(),
      invalidate({ resource: "knowledge-versions", id, invalidates: ["detail"] }),
      invalidate({ resource: "knowledge-versions", invalidates: ["list"] }),
    ]);
  }

  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ErrorState error={query.error} />;

  return (
    <>
      <PageHeading
        title={document.title}
        description={`${document.document_key} · revision ${document.revision_number} · version ${document.dataset_version}`}
        actions={
          <Link className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm" href={`/admin/knowledge/${id}`}>
            <ArrowLeft size={16} /> Back to version
          </Link>
        }
      />
      <Panel className="mb-5">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge value={document.version_status} />
          <span className="font-mono text-xs text-slate-500">SHA-256 {document.checksum.slice(0, 16)}…</span>
          <span className="ml-auto text-xs text-slate-500">{document.source_path}</span>
        </div>
      </Panel>

      <form className="grid gap-5" onSubmit={(event) => void save(event)}>
        <div className="grid gap-5 xl:grid-cols-2">
          <Panel>
            <div className="mb-3 flex items-center justify-between gap-2">
              <h2 className="font-semibold">Markdown</h2>
              {canEdit ? (
                <Button disabled={action.mutation.isPending || content === document.content_markdown} size="sm" type="submit">
                  <Save size={15} /> Save revision
                </Button>
              ) : null}
            </div>
            <textarea
              className="min-h-[38rem] w-full resize-y rounded-xl border border-slate-300 bg-slate-950 p-4 font-mono text-xs leading-5 text-slate-100 outline-none focus:border-teal-500"
              disabled={!canEdit}
              maxLength={250000}
              onChange={(event) =>
                setEdits((current) => ({ ...current, [document.checksum]: event.target.value }))
              }
              spellCheck={false}
              value={content}
            />
          </Panel>
          <Panel>
            <h2 className="mb-3 font-semibold">Safe preview</h2>
            <article className="max-h-[38rem] overflow-auto text-sm leading-7 text-slate-700 [&_h1]:mb-4 [&_h1]:text-2xl [&_h1]:font-bold [&_h2]:mb-2 [&_h2]:mt-6 [&_h2]:text-lg [&_h2]:font-semibold [&_li]:ml-5 [&_li]:list-disc [&_p]:my-3 [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:p-2 [&_th]:border [&_th]:p-2">
              <ReactMarkdown>{markdownBody(content)}</ReactMarkdown>
            </article>
          </Panel>
        </div>
      </form>

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,.7fr)_minmax(0,1.3fr)]">
        <Panel>
          <div className="mb-3 flex items-center gap-2">
            <FileSearch className="text-teal-700" size={18} />
            <h2 className="font-semibold">Evaluation impact</h2>
          </div>
          <p className="text-xs font-medium text-slate-500">Factos relacionados</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {document.dependencies.fact_ids.map((fact) => (
              <span className="rounded-md bg-slate-100 px-2 py-1 font-mono text-[11px]" key={fact}>{fact}</span>
            ))}
          </div>
          <p className="mt-4 text-xs font-medium text-slate-500">
            {document.dependencies.evaluation_case_ids.length} casos dependentes
          </p>
          <details className="mt-2 text-xs text-slate-600">
            <summary className="cursor-pointer">Ver identificadores</summary>
            <ul className="mt-2 grid gap-1 font-mono">
              {document.dependencies.evaluation_case_ids.map((caseId) => <li key={caseId}>{caseId}</li>)}
            </ul>
          </details>
          <p className="mt-5 text-xs font-medium text-slate-500">Immutable history</p>
          <ul className="mt-2 grid gap-1 text-xs text-slate-600">
            {document.revisions.map((revision) => (
              <li className="flex justify-between gap-2" key={revision.revision_number}>
                <span>Revision {revision.revision_number}</span>
                <span className="font-mono">{revision.content_checksum.slice(0, 10)}…</span>
              </li>
            ))}
          </ul>
        </Panel>
        <div className="grid gap-3">
          {document.chunks.map((chunk) => (
            <Panel key={chunk.id}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="font-semibold">{chunk.section}</h2>
                <span className="font-mono text-xs text-slate-500">#{chunk.ordinal} · {chunk.token_count} tokens</span>
              </div>
              <p className="mt-2 text-xs text-slate-500">{chunk.section_path.join(" › ")}</p>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-700">{chunk.content}</p>
            </Panel>
          ))}
        </div>
      </div>
    </>
  );
}
