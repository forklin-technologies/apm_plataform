import { StatusPill } from "@/components/ui/StatusPill";
import type { Movement } from "@/lib/api/types";
import { formatDateShort } from "@/lib/format";
import { formatBRL } from "@/lib/money";
import { KIND_LABELS, movementStatusView } from "@/lib/status";
import { KIND_META } from "./kinds";

function MovementRow({ movement }: { movement: Movement }) {
  const status = movementStatusView(movement.kind, movement.status);
  const meta = KIND_META[movement.kind];
  const inactive = movement.status === "CANCELLED" || movement.status === "EXPIRED" || movement.status === "REJECTED";
  // O sentido (entrada ou saida) vem da camada de dados.
  const sign = movement.direction === "IN" ? "+" : "−";

  return (
    <li className="flex items-center gap-3.5 px-4 py-3.5 sm:px-5">
      <span aria-hidden="true" className={`grid size-10 shrink-0 place-items-center rounded-[12px] ${meta.tile}`}>
        {meta.icon}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-body font-semibold leading-snug text-ink [overflow-wrap:anywhere]">{movement.title}</p>
        <p className="mt-0.5 text-sub text-ink-2 [overflow-wrap:anywhere]">
          <span className="font-medium text-ink">{KIND_LABELS[movement.kind]}</span> · {movement.counterparty} ·{" "}
          {formatDateShort(movement.occurredAt)}
        </p>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1.5">
        <p
          className={`text-body font-semibold tabular-nums ${inactive ? "text-ink-3 line-through decoration-1" : "text-ink"}`}
        >
          <span className="sr-only">{movement.direction === "IN" ? "Entrada de " : "Saída de "}</span>
          <span aria-hidden="true">{sign}</span>
          {" "}
          {formatBRL(movement.amountCents)}
        </p>
        <StatusPill tone={status.tone}>{status.label}</StatusPill>
      </div>
    </li>
  );
}

export function MovementList({ movements, label }: { movements: Movement[]; label: string }) {
  return (
    <ul
      aria-label={label}
      className="divide-y divide-line overflow-hidden rounded-[var(--r-lg)] bg-surface shadow-[0_0_0_1px_var(--line)]"
    >
      {movements.map((movement) => (
        <MovementRow key={movement.id} movement={movement} />
      ))}
    </ul>
  );
}
