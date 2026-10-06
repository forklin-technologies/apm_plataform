import type { StatementSummary } from "@/lib/api/types";
import { formatBRL } from "@/lib/money";
import { periodLabel } from "@/lib/statement-labels";

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="px-5 py-4">
      <dt className="text-sub text-ink-2">{label}</dt>
      <dd className="mt-1 text-heading tabular-nums text-ink">{value}</dd>
      <dd className="mt-0.5 text-foot text-ink-2">{note}</dd>
    </div>
  );
}

/**
 * Resumo do mes. Todos os valores sao colunas de statement_summary: nada e somado aqui. Os DOIS
 * saldos aparecem com nome proprio: "em caixa" (principal) e "apos reembolsos pendentes" (secundario).
 */
export function StatementIndicators({ summary }: { summary: StatementSummary }) {
  return (
    <section aria-labelledby="indicators-title" className="rounded-[var(--r-lg)] bg-surface shadow-[0_0_0_1px_var(--line)]">
      {/* @container: o numero escala com a largura do cartao (cqw), entao nunca estoura a 320px. */}
      <div className="@container px-5 pb-5 pt-6 sm:px-6">
        <h2 id="indicators-title" className="text-sub text-ink-2">
          Saldo em caixa em {periodLabel(summary.period)}
        </h2>
        <p className="mt-1.5 text-[min(3.5rem,14cqw)] font-bold leading-none tracking-[-0.045em] text-ink">
          {formatBRL(summary.closingBalanceCents)}
        </p>
        <p className="mt-2 max-w-[44ch] text-foot text-ink-2">
          O que há no caixa agora: saldo inicial mais as entradas, menos as saídas já pagas.
        </p>
        <p className="mt-3 rounded-[var(--r-md)] bg-neutral-soft px-3 py-2 text-sub text-ink-2">
          Após reembolsos pendentes:{" "}
          <strong className="font-semibold tabular-nums text-ink">{formatBRL(summary.balanceAfterPendingCents)}</strong>
          <span className="block text-foot">
            É o saldo em caixa menos {formatBRL(summary.pendingReimbursementsCents)} de reembolsos ainda a pagar.
          </span>
        </p>
      </div>
      <dl className="grid grid-cols-1 divide-y divide-line border-t border-line sm:grid-cols-2 sm:divide-y-0 sm:[&>div:nth-child(n+3)]:border-t sm:[&>div:nth-child(even)]:border-l sm:[&>div]:border-line">
        <Stat
          label="Saldo inicial"
          value={formatBRL(summary.openingBalanceCents)}
          note="Caixa na virada do mês anterior."
        />
        <Stat
          label="Entradas"
          value={formatBRL(summary.totalInCents)}
          note={`Contribuições ${formatBRL(summary.contributionsInCents)} · outras ${formatBRL(summary.otherInCents)} · devoluções recebidas ${formatBRL(summary.refundsInCents)}`}
        />
        <Stat
          label="Saídas pagas"
          value={formatBRL(summary.totalOutCents)}
          note={`Despesas ${formatBRL(summary.expensesOutCents)} · reembolsos ${formatBRL(summary.reimbursementsOutCents)}`}
        />
        <Stat
          label="Lançamentos"
          value={String(summary.entriesCount)}
          note={summary.entriesCount === 1 ? "lançamento no mês" : "lançamentos no mês"}
        />
      </dl>
    </section>
  );
}
