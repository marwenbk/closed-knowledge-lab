"use client";

import { useGetIdentity, useLogout } from "@refinedev/core";
import { BookOpen, LayoutDashboard, LogOut, Menu, MessagesSquare, SlidersHorizontal, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import type { AdminIdentity } from "@/lib/admin-types";

const navigation = [
  { href: "/admin", label: "Visão geral", icon: LayoutDashboard, roles: [] },
  {
    href: "/admin/handoffs",
    label: "Atendimentos",
    icon: MessagesSquare,
    roles: ["ADMIN", "SUPERVISOR", "SUPPORT_AGENT"],
  },
  {
    href: "/admin/knowledge",
    label: "Base de conhecimento",
    icon: BookOpen,
    roles: [
      "ADMIN",
      "SUPERVISOR",
      "SUPPORT_AGENT",
      "HUMAN_REVIEWER",
      "KNOWLEDGE_EDITOR",
      "AUDITOR",
    ],
  },
  {
    href: "/admin/tuning",
    label: "Ajustes",
    icon: SlidersHorizontal,
    roles: ["ADMIN", "SUPERVISOR", "HUMAN_REVIEWER", "KNOWLEDGE_EDITOR", "AUDITOR"],
  },
];

function Navigation({ roles, close }: { roles: string[]; close?: () => void }) {
  const pathname = usePathname();
  return (
    <nav className="grid gap-1" aria-label="Navegação administrativa">
      {navigation.filter((item) => item.roles.length === 0 || item.roles.some((role) => roles.includes(role))).map(({ href, label, icon: Icon }) => {
        const active = href === "/admin" ? pathname === href : pathname.startsWith(href);
        return (
          <Link
            className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium ${
              active ? "bg-teal-50 text-teal-800" : "text-slate-600 hover:bg-slate-100"
            }`}
            href={href}
            key={href}
            onClick={close}
          >
            <Icon aria-hidden size={18} />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

export function AdminShell({ children }: { children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const identity = useGetIdentity<AdminIdentity>();
  const logout = useLogout();
  const roles = identity.data?.roles ?? [];

  return (
    <div className="min-h-screen bg-slate-50 text-slate-950">
      <aside className="fixed inset-y-0 left-0 hidden w-64 border-r border-slate-200 bg-white p-5 lg:block">
        <Link className="mb-8 flex items-center gap-3" href="/admin">
          <span className="grid size-10 place-items-center rounded-xl bg-teal-700 font-bold text-white">
            T
          </span>
          <span>
            <strong className="block text-lg">TopMed</strong>
            <span className="text-xs text-slate-500">Operações</span>
          </span>
        </Link>
        <Navigation roles={roles} />
      </aside>

      {menuOpen ? (
        <div className="fixed inset-0 z-40 bg-slate-950/35 lg:hidden" role="presentation">
          <aside className="h-full w-72 bg-white p-5 shadow-xl">
            <div className="mb-8 flex items-center justify-between">
              <strong>TopMed Operações</strong>
              <Button
                aria-label="Fechar menu"
                onClick={() => setMenuOpen(false)}
                size="icon"
                type="button"
                variant="ghost"
              >
                <X size={20} />
              </Button>
            </div>
            <Navigation close={() => setMenuOpen(false)} roles={roles} />
          </aside>
        </div>
      ) : null}

      <div className="lg:pl-64">
        <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-slate-200 bg-white/95 px-4 backdrop-blur sm:px-8">
          <Button
            aria-label="Abrir menu"
            className="lg:hidden"
            onClick={() => setMenuOpen(true)}
            size="icon"
            type="button"
            variant="ghost"
          >
            <Menu size={20} />
          </Button>
          <div className="ml-auto flex items-center gap-3">
            <div className="hidden text-right sm:block">
              <span className="block text-sm font-medium">
                {identity.data?.display_name ?? "Operador"}
              </span>
              <span className="block text-xs text-slate-500">
                {identity.data?.roles.join(" · ")}
              </span>
            </div>
            <Button
              disabled={logout.isPending}
              onClick={() => logout.mutate({})}
              size="sm"
              type="button"
              variant="outline"
            >
              <LogOut size={16} />
              Sair
            </Button>
          </div>
        </header>
        <main className="mx-auto w-full max-w-7xl p-4 sm:p-8">{children}</main>
      </div>
    </div>
  );
}
