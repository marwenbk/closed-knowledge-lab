"use client";

import { useCustom, useSubscription } from "@refinedev/core";
import { Activity, BookOpen, Clock3, MessagesSquare } from "lucide-react";
import Link from "next/link";

import {
  ErrorState,
  formatDuration,
  formatDate,
  LoadingState,
  PageHeading,
  Panel,
  StatusBadge,
} from "@/components/admin-ui";
import { Button } from "@/components/ui/button";
import type { Dashboard } from "@/lib/admin-types";

function Metric({ label, value, detail }: { label: string; value: string | number; detail: string }) {
  return (
    <Panel>
      <p className="text-sm font-medium text-slate-500">{label}</p>
      <p className="mt-2 text-3xl font-bold tracking-tight">{value}</p>
      <p className="mt-1 text-xs text-slate-500">{detail}</p>
    </Panel>
  );
}

export default function AdminDashboardPage() {
  const { result, query } = useCustom<Dashboard>({
    url: "/api/v1/admin/dashboard",
    method: "get",
  });
  useSubscription({
    channel: "resources/dashboard",
    onLiveEvent: () => void query.refetch(),
  });

  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ErrorState error={query.error} />;
  const dashboard = result.data;

  return (
    <>
      <PageHeading
        title="Visão geral"
        description="Estado operacional do atendimento, da base ativa e da qualidade das respostas."
        actions={
          <Button
            onClick={() => void query.refetch()}
            type="button"
            variant="outline"
          >
            Atualizar
          </Button>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          detail={`${dashboard.conversations.assigned} atribuídos · ${dashboard.conversations.human_active} ativos`}
          label="Conversas abertas"
          value={dashboard.conversations.open}
        />
        <Metric
          detail={`Mais antiga: ${formatDuration(dashboard.conversations.oldest_waiting_seconds)}`}
          label="A aguardar pessoa"
          value={dashboard.conversations.waiting}
        />
        <Metric
          detail={`${dashboard.knowledge.chunk_count} blocos indexados`}
          label="Documentos ativos"
          value={dashboard.knowledge.document_count}
        />
        <Metric
          detail="Do pedido à primeira atribuição"
          label="Espera média"
          value={`${Math.round(dashboard.quality.average_handoff_wait_seconds)}s`}
        />
      </div>

      {dashboard.pending_reviews.length ? (
        <Panel className="mt-6 border-sky-200">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h2 className="font-semibold">Respostas aguardando revisão</h2>
              <p className="mt-1 text-sm text-slate-500">
                Propostas verificadas permanecem privadas até uma decisão humana.
              </p>
            </div>
            <StatusBadge value={`${dashboard.pending_reviews.length} PENDING`} />
          </div>
          <div className="mt-4 grid gap-3">
            {dashboard.pending_reviews.map((review) => (
              <Link
                className="rounded-xl border border-slate-200 p-3 transition hover:border-sky-300 hover:bg-sky-50/40"
                href={`/admin/conversations/${review.conversation_id}`}
                key={review.message_id}
              >
                <div className="flex items-center justify-between gap-3">
                  <StatusBadge value={review.status} />
                  <span className="text-xs text-slate-500">{formatDate(review.created_at)}</span>
                </div>
                <p className="mt-2 line-clamp-2 text-sm text-slate-700">{review.content}</p>
              </Link>
            ))}
          </div>
        </Panel>
      ) : null}

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <Panel>
          <div className="mb-5 flex items-center gap-2">
            <Activity className="text-teal-700" size={20} />
            <h2 className="font-semibold">Qualidade do assistente</h2>
          </div>
          <dl className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-slate-500">Latência AI média</dt>
              <dd className="mt-1 text-xl font-semibold">
                {Math.round(dashboard.quality.average_ai_latency_ms)} ms
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Recusas</dt>
              <dd className="mt-1 text-xl font-semibold">
                {dashboard.quality.refusal_rate_percent}%
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Conflitos</dt>
              <dd className="mt-1 text-xl font-semibold">
                {dashboard.quality.conflict_rate_percent}%
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Falhas de grounding</dt>
              <dd className="mt-1 text-xl font-semibold">
                {dashboard.quality.grounding_failure_rate_percent}%
              </dd>
            </div>
          </dl>
          <div className="mt-5 flex flex-wrap gap-2 border-t border-slate-100 pt-4">
            {Object.entries(dashboard.quality.answerability).map(([status, count]) => (
              <span className="rounded-lg bg-slate-100 px-2.5 py-1 text-xs" key={status}>
                {status.replaceAll("_", " ")}: {count}
              </span>
            ))}
          </div>
        </Panel>

        <Panel>
          <div className="mb-5 flex items-center gap-2">
            <BookOpen className="text-teal-700" size={20} />
            <h2 className="font-semibold">Versões em execução</h2>
          </div>
          <dl className="grid gap-3 text-sm">
            <div className="flex items-center justify-between gap-4">
              <dt className="text-slate-500">Base de conhecimento</dt>
              <dd className="flex items-center gap-2 font-medium">
                {dashboard.knowledge.dataset_id}:{dashboard.knowledge.dataset_version}
                <StatusBadge value={dashboard.knowledge.status} />
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-slate-500">Modelo</dt>
              <dd className="font-medium">{dashboard.runtime.model}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-slate-500">Prompt</dt>
              <dd className="font-mono text-xs">{dashboard.runtime.prompt_version}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-slate-500">Embeddings</dt>
              <dd className="max-w-64 truncate font-mono text-xs">
                {dashboard.runtime.embedding_version}
              </dd>
            </div>
          </dl>
          <div className="mt-5 border-t border-slate-100 pt-4 text-sm">
            <div className="flex items-center gap-2 font-medium">
              <Clock3 size={16} /> Última avaliação
            </div>
            {dashboard.latest_evaluation ? (
              <p className="mt-2 text-slate-600">
                {dashboard.latest_evaluation.mode ?? "suite"} · {" "}
                {dashboard.latest_evaluation.evaluated_cases ?? 0} casos · {" "}
                <StatusBadge
                  value={dashboard.latest_evaluation.passed ? "PASSED" : "FAILED"}
                />
              </p>
            ) : (
              <p className="mt-2 text-slate-500">Ainda não há relatório local.</p>
            )}
          </div>
        </Panel>
      </div>

      <p className="mt-6 flex items-center gap-2 text-xs text-slate-500">
        <MessagesSquare size={14} /> Atualização automática por eventos da fila.
      </p>
    </>
  );
}
