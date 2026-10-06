"use client";

import Link from "next/link";
import { areaHref, areasFor } from "@/lib/permissions";
import { ExpensesBoard } from "./ExpensesBoard";

interface Props {
  schoolId: string;
  userId: string;
  permissions: string[];
  /** `?aba=minhas`: quem tem as duas funcoes (diretor) escolhe entre a fila e as proprias despesas. */
  tab: "minhas" | "fila";
}

/**
 * Area "Despesas". Professora: so "Minhas despesas". Gestao (tesoureira): so a fila de analise. Quem tem as
 * duas funcoes (diretor): abas. As permissoes vem da sessao; a API confere de novo.
 */
export function ExpensesArea({ schoolId, userId, permissions, tab }: Props) {
  const areas = areasFor(permissions);
  if (!areas.manageExpenses && !areas.myExpenses) {
    return (
      <p role="note" className="rounded-[var(--r-md)] bg-info-soft px-4 py-3 text-sub text-info">
        O seu perfil não tem acesso às despesas desta escola.
      </p>
    );
  }
  const both = areas.manageExpenses && areas.myExpenses;
  const scope = both ? (tab === "minhas" ? "mine" : "queue") : areas.manageExpenses ? "queue" : "mine";
  return (
    <div>
      {both && (
        <nav aria-label="Despesas" className="mb-5 flex gap-2">
          {([["fila", "Fila de análise", areaHref("despesas")], ["minhas", "Minhas despesas", areaHref("despesas", { tab: "minhas" })]] as const).map(([id, label, href]) => (
            <Link
              key={id}
              href={href}
              aria-current={(tab === "minhas" ? "minhas" : "fila") === id ? "page" : undefined}
              className={`inline-flex min-h-11 items-center rounded-full px-4 text-sub font-semibold ${(tab === "minhas" ? "minhas" : "fila") === id ? "bg-ink text-bg" : "bg-neutral-soft text-ink"}`}
            >
              {label}
            </Link>
          ))}
        </nav>
      )}
      <ExpensesBoard key={scope} schoolId={schoolId} userId={userId} permissions={permissions} scope={scope} />
    </div>
  );
}
