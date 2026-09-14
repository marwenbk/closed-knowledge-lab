import { Suspense, type ReactNode } from "react";

import { AdminProvider } from "@/providers/admin-provider";

export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<main className="grid min-h-screen place-items-center">Loading…</main>}>
      <AdminProvider>{children}</AdminProvider>
    </Suspense>
  );
}
