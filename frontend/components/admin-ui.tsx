import type { ReactNode } from "react";

export function PageHeading({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{title}</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">{description}</p>
      </div>
      {actions}
    </div>
  );
}

export function Panel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-2xl border border-slate-200 bg-white p-5 shadow-sm ${className}`}>
      {children}
    </section>
  );
}

export function StatusBadge({ value }: { value: string | null | undefined }) {
  const normalized = value ?? "—";
  const attention = ["URGENT", "HIGH", "CONFLICTING_EVIDENCE", "FAILED", "CLOSED"].some(
    (item) => normalized.includes(item),
  );
  const positive = ["ACTIVE", "ANSWERABLE", "COMPLETED", "PASSED"].some((item) =>
    normalized.includes(item),
  );
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${
        attention
          ? "bg-red-50 text-red-700"
          : positive
            ? "bg-teal-50 text-teal-700"
            : "bg-amber-50 text-amber-700"
      }`}
    >
      {normalized.replaceAll("_", " ")}
    </span>
  );
}

export function LoadingState() {
  return <p className="py-12 text-center text-sm text-slate-500">A carregar…</p>;
}

export function ErrorState({ error }: { error: unknown }) {
  return (
    <p className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
      {error instanceof Error ? error.message : "Não foi possível carregar os dados."}
    </p>
  );
}

export function formatDate(value: string | null | undefined): string {
  return value
    ? new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(
        new Date(value),
      )
    : "—";
}

export function formatDuration(totalSeconds: number): string {
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  if (minutes < 60) return `${minutes}min`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}min`;
}
