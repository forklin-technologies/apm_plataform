"use client";

import type { Ref } from "react";
import { Button } from "@/components/ui/Button";
import { LockIcon } from "@/components/ui/icons";
import { formatBRL } from "@/lib/money";
import { FIELD_LABELS, IDENTIFICATION_FIELDS, type IdentificationValues } from "@/lib/validation";
import { StickyActions } from "./FlowChrome";

interface ReviewStepProps {
  schoolName: string;
  description: string;
  amountCents: number;
  identification: IdentificationValues;
  submitting: boolean;
  error: string | null;
  headingRef: Ref<HTMLHeadingElement>;
  onEditAmount: () => void;
  onEditIdentification: (() => void) | null;
  onBack: () => void;
  onSubmit: () => void;
}

function Row({ label, value, onEdit, editLabel }: { label: string; value: string; onEdit?: (() => void) | null; editLabel?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 px-5 py-3">
      <div className="min-w-0">
        <dt className="text-foot text-ink-2">{label}</dt>
        <dd className="text-body font-medium text-ink [overflow-wrap:anywhere]">{value}</dd>
      </div>
      {onEdit && (
        <button
          type="button"
          onClick={onEdit}
          aria-label={editLabel}
          className="inline-flex min-h-11 shrink-0 items-center rounded-full px-3 text-sub font-semibold text-accent-ink hover:bg-neutral-soft"
        >
          Alterar
        </button>
      )}
    </div>
  );
}

export function ReviewStep({
  schoolName,
  description,
  amountCents,
  identification,
  submitting,
  error,
  headingRef,
  onEditAmount,
  onEditIdentification,
  onBack,
  onSubmit,
}: ReviewStepProps) {
  const filled = IDENTIFICATION_FIELDS.filter((field) => identification[field]);
  return (
    <div className="step-in">
      <h2 ref={headingRef} tabIndex={-1} className="text-title text-ink outline-none">
        Confira antes de pagar
      </h2>
      <p className="mt-2 max-w-[52ch] text-body text-ink-2">
        Se estiver tudo certo, vamos gerar o Pix. Você só paga depois de abrir o app do seu banco.
      </p>

      <dl className="mt-6 divide-y divide-line overflow-hidden rounded-[var(--r-lg)] bg-surface shadow-[0_0_0_1px_var(--line)]">
        <Row label="Escola" value={schoolName} />
        <Row label="Contribuição" value={description} onEdit={onEditAmount} editLabel="Alterar o valor" />
        <Row label="Valor" value={formatBRL(amountCents)} />
        {filled.map((field) => (
          <Row
            key={field}
            label={FIELD_LABELS[field]}
            value={identification[field] ?? ""}
            onEdit={onEditIdentification}
            editLabel={`Alterar ${FIELD_LABELS[field].toLowerCase()}`}
          />
        ))}
      </dl>

      <p className="mt-4 flex items-start gap-2.5 text-sub text-ink-2">
        <LockIcon size={18} className="mt-0.5 shrink-0" />
        O Pix vai direto para a conta da APM. A plataforma não guarda o dinheiro e quem confirma o pagamento é o banco.
      </p>

      <div aria-live="polite">
        {error && (
          <p role="alert" className="mt-4 rounded-[var(--r-md)] bg-bad-soft px-4 py-3 text-sub font-medium text-bad">
            {error}
          </p>
        )}
      </div>

      <StickyActions>
        <Button variant="secondary" className="shrink-0 sm:min-w-32" onClick={onBack} disabled={submitting}>
          Voltar
        </Button>
        <Button className="flex-1 sm:min-w-56 sm:flex-none" onClick={onSubmit} disabled={submitting} aria-busy={submitting}>
          {submitting ? "Gerando o Pix…" : `Gerar Pix de ${formatBRL(amountCents)}`}
        </Button>
      </StickyActions>
    </div>
  );
}
