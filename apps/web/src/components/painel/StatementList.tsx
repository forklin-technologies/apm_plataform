import { StatusPill } from "@/components/ui/StatusPill";
import type { PendingEntry, StatementEntry } from "@/lib/api/types";
import { formatBRL } from "@/lib/money";
import { KIND_LABELS } from "@/lib/status";
import { categoryLabel, entryStatus, formatLocalDateShort, pendingStatus } from "@/lib/statement-labels";
import { KIND_META } from "./kinds";

function EntryRow({ entry }: { entry: StatementEntry }) {
  const status = entryStatus(entry.kind, entry.status, entry.statusLabel);
  const meta = KIND_META[entry.kind];
  const counterparty =
    entry.beneficiaryLabel ?? entry.originLabel ?? (entry.kind === "CONTRIBUTION" ? "Contribuinte anônimo" : null);
  // O sentido (entrada ou saida) vem da API.
  const sign = entry.direction === "IN" ? "+" : "−";
  return (
    <li className="flex items-center gap-3.5 px-4 py-3.5 sm:px-5">
      <span aria-hidden="true" className={`grid size-10 shrink-0 place-items-center rounded-[12px] ${meta.tile}`}>
        {meta.icon}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-body font-semibold leading-snug text-ink [overflow-wrap:anywhere]">
          {entry.description ?? categoryLabel(entry.categoryKey)}
        </p>
        <p className="mt-0.5 text-sub text-ink-2 [overflow-wrap:anywhere]">
          <span className="font-medium text-ink">{KIND_LABELS[entry.kind]}</span>
          {counterparty ? ` · ${counterparty}` : ""} · {formatLocalDateShort(entry.localDate)} · nº {entry.referenceCode}
          {entry.lateAdjustment ? " · ajuste tardio" : ""}
        </p>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1.5">
        <p className="text-body font-semibold tabular-nums text-ink">
          <span className="sr-only">{entry.direction === "IN" ? "Entrada de " : "Saída de "}</span>
          <span aria-hidden="true">{sign}</span> {formatBRL(entry.amountCents)}
        </p>
        <StatusPill tone={status.tone}>{status.label}</StatusPill>
        <p className="text-foot tabular-nums text-ink-2">
          <span className="sr-only">Saldo depois deste lançamento: </span>
          saldo {formatBRL(entry.runningBalanceCents)}
        </p>
      </div>
    </li>
  );
}

export function StatementList({ entries, label }: { entries: StatementEntry[]; label: string }) {
  return (
    <ul aria-label={label} className="divide-y divide-line overflow-hidden rounded-[var(--r-lg)] bg-surface shadow-[0_0_0_1px_var(--line)]">
      {entries.map((entry) => (
        <EntryRow key={entry.transactionId} entry={entry} />
      ))}
    </ul>
  );
}

export function PendingRows({ entries, label }: { entries: PendingEntry[]; label: string }) {
  return (
    <ul aria-label={label} className="divide-y divide-line overflow-hidden rounded-[var(--r-lg)] bg-surface shadow-[0_0_0_1px_var(--line)]">
      {entries.map((entry) => {
        const meta = KIND_META[entry.kind];
        return (
          <li key={entry.transactionId} className="flex items-center gap-3.5 px-4 py-3 sm:px-5">
            <span aria-hidden="true" className={`grid size-9 shrink-0 place-items-center rounded-[11px] ${meta.tile}`}>
              {meta.icon}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-body font-medium text-ink">
                {KIND_LABELS[entry.kind]} nº {entry.referenceCode}
              </p>
              <p className="text-sub text-ink-2">{pendingStatus(entry.status)}</p>
            </div>
            <p className="shrink-0 text-body font-semibold tabular-nums text-ink">
              <span className="sr-only">{entry.direction === "IN" ? "A receber: " : "A pagar: "}</span>
              {formatBRL(entry.amountCents)}
            </p>
          </li>
        );
      })}
    </ul>
  );
}
