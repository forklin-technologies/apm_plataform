import type { ReactNode } from "react";
import { formatBRL } from "@/lib/money";
import { FIELD_LABELS, IDENTIFICATION_FIELDS, type IdentificationValues } from "@/lib/validation";

export type Step = "amount" | "identification" | "review" | "pix";

export const STEP_LABELS: Record<Step, string> = {
  amount: "Valor",
  identification: "Seus dados",
  review: "Revisão",
  pix: "Pix",
};

/** Progresso real: as etapas formam uma sequencia. Mostra as etapas que a escola usa. */
export function StepProgress({ steps, current }: { steps: Step[]; current: Step }) {
  const index = Math.max(0, steps.indexOf(current));
  return (
    <div className="mb-8">
      <ol className="flex gap-1.5" aria-hidden="true">
        {steps.map((step, i) => (
          <li key={step} className={`h-1.5 flex-1 rounded-full transition-colors ${i <= index ? "bg-accent" : "bg-neutral-soft"}`} />
        ))}
      </ol>
      <p className="mt-2.5 text-sub text-ink-2" aria-live="polite">
        Etapa {index + 1} de {steps.length}: <span className="font-semibold text-ink">{STEP_LABELS[current]}</span>
      </p>
    </div>
  );
}

export function SummaryCard({
  schoolName,
  amountCents,
  description,
  identification,
  children,
}: {
  schoolName: string;
  amountCents: number | null;
  description: string | null;
  identification: IdentificationValues;
  children?: ReactNode;
}) {
  const filled = IDENTIFICATION_FIELDS.filter((field) => identification[field]);
  return (
    <aside
      aria-label="Resumo da contribuição"
      className="rounded-[var(--r-lg)] bg-surface p-6 shadow-[0_0_0_1px_var(--line)]"
    >
      <p className="text-sub text-ink-2">Sua contribuição para</p>
      <p className="text-headline text-ink">{schoolName}</p>
      <p className="mt-5 text-[2.25rem] font-bold leading-none tracking-[-0.035em] text-ink">
        {amountCents === null ? "R$ —" : formatBRL(amountCents)}
      </p>
      <p className="mt-1.5 min-h-5 text-sub text-ink-2">{description ?? "Escolha o valor para começar."}</p>
      {filled.length > 0 && (
        <dl className="mt-5 space-y-2 border-t border-line pt-4 text-sub">
          {filled.map((field) => (
            <div key={field} className="flex justify-between gap-4">
              <dt className="text-ink-2">{FIELD_LABELS[field]}</dt>
              <dd className="min-w-0 text-right font-medium text-ink">{identification[field]}</dd>
            </div>
          ))}
        </dl>
      )}
      {children}
    </aside>
  );
}

/** Barra de acao: fixa embaixo no celular (alcance do polegar), inline no desktop. */
export function StickyActions({ children }: { children: ReactNode }) {
  return (
    <div className="bar-material sticky bottom-0 z-20 -mx-5 mt-8 flex items-stretch gap-3 border-t border-line px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:-mx-8 sm:justify-end sm:px-8 lg:static lg:mx-0 lg:border-0 lg:bg-transparent lg:px-0 lg:py-0 lg:[backdrop-filter:none]">
      {children}
    </div>
  );
}
