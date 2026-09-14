"use client";

import { useLogin } from "@refinedev/core";
import { LockKeyhole } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";

type Credentials = { email: string; password: string };

export default function AdminLoginPage() {
  const login = useLogin<Credentials>();
  const [credentials, setCredentials] = useState<Credentials>({ email: "", password: "" });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    login.mutate(credentials);
  }

  return (
    <main className="grid min-h-screen place-items-center p-4">
      <section className="w-full max-w-md rounded-3xl border border-slate-200 bg-white p-8 shadow-xl shadow-teal-950/5">
        <div className="mb-8 flex items-center gap-3">
          <span className="grid size-12 place-items-center rounded-2xl bg-teal-700 text-white">
            <LockKeyhole aria-hidden size={22} />
          </span>
          <div>
            <h1 className="text-2xl font-bold">Closed-Knowledge Lab Operações</h1>
            <p className="text-sm text-slate-500">Acesso reservado à equipa de atendimento.</p>
          </div>
        </div>

        <form className="grid gap-5" onSubmit={submit}>
          <label className="grid gap-2 text-sm font-medium">
            E-mail
            <input
              autoComplete="username"
              autoFocus
              className="rounded-xl border border-slate-300 px-3 py-2.5 outline-none focus:border-teal-600 focus:ring-2 focus:ring-teal-100"
              onChange={(event) =>
                setCredentials((current) => ({ ...current, email: event.target.value }))
              }
              required
              type="email"
              value={credentials.email}
            />
          </label>
          <label className="grid gap-2 text-sm font-medium">
            Palavra-passe
            <input
              autoComplete="current-password"
              className="rounded-xl border border-slate-300 px-3 py-2.5 outline-none focus:border-teal-600 focus:ring-2 focus:ring-teal-100"
              minLength={12}
              onChange={(event) =>
                setCredentials((current) => ({ ...current, password: event.target.value }))
              }
              required
              type="password"
              value={credentials.password}
            />
          </label>
          {login.error ? (
            <p className="rounded-xl bg-red-50 p-3 text-sm text-red-700" role="alert">
              {login.error.message}
            </p>
          ) : null}
          <Button
            className="h-12 w-full"
            disabled={login.isPending}
            type="submit"
          >
            {login.isPending ? "A autenticar…" : "Entrar"}
          </Button>
        </form>
      </section>
    </main>
  );
}
