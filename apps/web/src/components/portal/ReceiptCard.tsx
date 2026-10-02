import { StatusPill } from "@/components/ui/StatusPill";
import type { Receipt } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";
import { formatBRL } from "@/lib/money";
import { pixStatusView } from "@/lib/status";
import { FIELD_LABELS, IDENTIFICATION_FIELDS } from "@/lib/validation";

/** Selo "Pago": so aparece quando o status devolvido pela camada de dados e PAID. */
export function PaidStamp({ animate = false, size = 112 }: { animate?: boolean; size?: number }) {
  return (
    <div
      aria-hidden="true"
      data-testid="paid-stamp"
      className={animate ? "stamp" : "-rotate-[8deg]"}
      style={{ width: size, height: size, color: "var(--ok)" }}
    >
      <svg viewBox="0 0 120 120" width={size} height={size} fill="none">
        {animate && (
          <circle
            className="stamp-ripple"
            data-decorative="true"
            cx="60"
            cy="60"
            r="50"
            stroke="currentColor"
            strokeWidth="2"
          />
        )}
        <circle
          data-part="ring"
          className={animate ? "stamp-ring" : undefined}
          cx="60"
          cy="60"
          r="52"
          stroke="currentColor"
          strokeWidth="3.5"
        />
        <circle cx="60" cy="60" r="44" stroke="currentColor" strokeWidth="1.2" strokeDasharray="2 5" opacity=".7" />
        <path
          data-part="check"
          className={animate ? "stamp-check" : undefined}
          d="M38 62l14 14 30-33"
          stroke="currentColor"
          strokeWidth="7"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
}

function ZigzagEdge() {
  return (
    <svg className="receipt-edge" width="100%" height="10" aria-hidden="true" focusable="false">
      <defs>
        <pattern id="receipt-teeth" width="16" height="10" patternUnits="userSpaceOnUse">
          <path d="M0 0H16L8 10Z" fill="currentColor" />
        </pattern>
      </defs>
      <rect width="100%" height="10" fill="url(#receipt-teeth)" />
    </svg>
  );
}

/** Comprovante de contribuicao. A cor vem do tema da escola (--accent) acima na arvore. */
export function ReceiptCard({ receipt, headingLevel = 2 }: { receipt: Receipt; headingLevel?: 2 | 3 }) {
  const status = pixStatusView(receipt.status);
  const Heading = `h${headingLevel}` as "h2" | "h3";
  const rows: Array<[string, string]> = [["Contribuição", receipt.description]];
  for (const field of IDENTIFICATION_FIELDS) {
    const value = receipt.identification[field];
    if (value) rows.push([FIELD_LABELS[field], value]);
  }
  if (receipt.paidAt) rows.push(["Pago em", formatDateTime(receipt.paidAt)]);

  return (
    <div className="receipt-wrap">
      <article className="receipt-sheet relative overflow-hidden" aria-label={`Comprovante número ${receipt.number}`}>
        <div className="h-2 bg-accent" aria-hidden="true" />
        <div className="px-6 pb-7 pt-6 sm:px-8">
          <header className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <Heading className="text-headline text-ink">{receipt.apmName}</Heading>
              <p className="mt-0.5 text-foot text-ink-2">Comprovante de contribuição</p>
            </div>
            <p className="shrink-0 text-right text-foot text-ink-2">
              Nº
              <br />
              <span className="font-semibold tabular-nums text-ink">{receipt.number}</span>
            </p>
          </header>

          <div className="my-5 border-t border-dashed border-line" aria-hidden="true" />

          <p className="text-foot text-ink-2">Valor</p>
          <p className="mt-1 text-[2.5rem] font-bold leading-none tracking-[-0.04em] text-ink">
            {formatBRL(receipt.amountCents)}
          </p>

          <dl className="mt-6 space-y-3 text-sub">
            {rows.map(([label, value]) => (
              <div key={label} className="flex items-baseline justify-between gap-5">
                <dt className="shrink-0 text-ink-2">{label}</dt>
                <dd className="min-w-0 text-right font-medium text-ink">{value}</dd>
              </div>
            ))}
            <div className="flex items-center justify-between gap-5 pt-1">
              <dt className="text-ink-2">Situação</dt>
              <dd>
                <StatusPill tone={status.tone}>{status.label}</StatusPill>
              </dd>
            </div>
          </dl>

          {receipt.status === "PAID" && (
            <div className="pointer-events-none absolute right-4 top-[5.6rem] opacity-90 sm:right-6">
              <PaidStamp size={96} />
            </div>
          )}
        </div>
      </article>
      <ZigzagEdge />
    </div>
  );
}
