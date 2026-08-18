import { Suspense, type ReactNode } from "react";

import { AdminShell } from "@/components/admin-shell";
import { AdminGuard } from "@/providers/admin-provider";

export default function ProtectedAdminLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<main className="grid min-h-screen place-items-center">A carregar…</main>}>
      <AdminGuard>
        <AdminShell>{children}</AdminShell>
      </AdminGuard>
    </Suspense>
  );
}
