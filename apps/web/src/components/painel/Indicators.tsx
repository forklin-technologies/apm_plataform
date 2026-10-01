import type { DashboardSummary } from "@/lib/api/types";
import { formatBRL } from "@/lib/money";

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="px-5 py-4">
      <dt className="text-sub text-ink-2">{label}</dt>
      <dd className="mt-1 text-heading tabular-nums text-ink">{value}</dd>
      <dd className="mt-0.5 text-foot text-ink-2">{note}</dd>
    </div>
  );
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** Indicadores do mes. Todos os totais chegam prontos do backend; nada e somado aqui. */
export function Indicators({ summary }: { summary: DashboardSummary }) {
  return (
    <section aria-labelledby="indicators-title" className="rounded-[var(--r-lg)] bg-surface shadow-[0_0_0_1px_var(--line)]">
      <div className="px-5 pb-5 pt-6 sm:px-6">
        <h2 id="indicators-title" className="text-sub text-ink-2">
          Saldo de {summary.periodLabel.split(" de ")[0]?.toLowerCase()}
        </h2>
        <p className="mt-1.5 text-[3rem] font-bold leading-none tracking-[-0.045em] text-ink sm:text-[3.5rem]">
          {formatBRL(summary.balanceCents)}
        </p>
        <p className="mt-2 max-w-[44ch] text-foot text-ink-2">
          Entradas menos despesas, reembolsos pagos e devoluções.
        </p>
      </div>
      <dl className="grid grid-cols-1 divide-y divide-line border-t border-line sm:grid-cols-2 sm:divide-y-0 sm:[&>div:nth-child(n+3)]:border-t sm:[&>div:nth-child(even)]:border-l sm:[&>div]:border-line">
        <Stat
          label="Arrecadado"
          value={formatBRL(summary.collectedCents)}
          note={plural(summary.collectedCount, "contribuição paga", "contribuições pagas")}
        />
        <Stat label="Despesas" value={formatBRL(summary.expensesCents)} note={plural(summary.expensesCount, "despesa", "despesas")} />
        <Stat
          label="Reembolsos pendentes"
          value={formatBRL(summary.reimbursementsPendingCents)}
          note={`${plural(summary.reimbursementsPendingCount, "em análise", "em análise")} · ${formatBRL(summary.reimbursementsPaidCents)} já pagos`}
        />
        <Stat label="Devoluções" value={formatBRL(summary.refundsCents)} note={plural(summary.refundsCount, "devolução", "devoluções")} />
      </dl>
    </section>
  );
}
