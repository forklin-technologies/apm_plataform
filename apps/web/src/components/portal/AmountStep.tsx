"use client";

import { useEffect, useRef, useState, type Ref } from "react";
import { CheckIcon } from "@/components/ui/icons";
import { TextField } from "@/components/ui/TextField";
import { Button } from "@/components/ui/Button";
import type { PublicSchool } from "@/lib/api/types";
import { centsFromDigits, formatBRL, formatBRLNumber, parsePastedAmount } from "@/lib/money";
import { StickyActions } from "./FlowChrome";

export const CUSTOM_CHOICE = "custom";

/** Identificador da opcao de um valor sugerido: o proprio valor em centavos. */
export const suggestedChoice = (cents: number) => `suggested-${cents}`;
export function suggestedCents(choice: string | null): number | null {
  const match = choice === null ? null : /^suggested-(\d+)$/.exec(choice);
  return match ? Number(match[1]) : null;
}

interface ChoiceRowProps {
  value: string;
  checked: boolean;
  title: string;
  description: string;
  trailing: string;
  onSelect: (value: string) => void;
}

function ChoiceRow({ value, checked, title, description, trailing, onSelect }: ChoiceRowProps) {
  return (
    <label
      className="group relative flex min-h-[4.5rem] cursor-pointer items-center gap-4 rounded-[var(--r-md)] bg-surface px-4 py-3.5 shadow-[0_0_0_1px_var(--line)] transition-shadow duration-150 has-[:checked]:bg-[color-mix(in_srgb,var(--accent)_9%,var(--surface))] has-[:checked]:shadow-[0_0_0_2px_var(--accent)] has-[:focus-visible]:outline has-[:focus-visible]:outline-[3px] has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-[var(--focus)]"
    >
      <input
        type="radio"
        name="amount-choice"
        value={value}
        checked={checked}
        onChange={() => onSelect(value)}
        className="sr-only"
      />
      <span
        aria-hidden="true"
        className="grid size-6 shrink-0 place-items-center rounded-full border-2 border-field-border text-accent-contrast transition-colors group-has-[:checked]:border-accent group-has-[:checked]:bg-accent"
      >
        <CheckIcon size={14} strokeWidth={3} className="opacity-0 group-has-[:checked]:opacity-100" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-body font-semibold leading-snug text-ink">{title}</span>
        <span className="mt-0.5 block text-sub text-ink-2">{description}</span>
      </span>
      <span className="shrink-0 text-body font-semibold tabular-nums text-ink">{trailing}</span>
    </label>
  );
}

interface AmountStepProps {
  school: PublicSchool;
  choice: string | null;
  customCents: number;
  error: string | null;
  headingRef: Ref<HTMLHeadingElement>;
  onChoice: (choice: string) => void;
  onCustomChange: (cents: number) => void;
  onContinue: () => void;
}

export function AmountStep({
  school,
  choice,
  customCents,
  error,
  headingRef,
  onChoice,
  onCustomChange,
  onContinue,
}: AmountStepProps) {
  const customRef = useRef<HTMLInputElement>(null);
  const previous = useRef(choice);
  const [pasteWarning, setPasteWarning] = useState<string | null>(null);

  // Colar ou soltar texto no campo: o valor e lido como REAIS ("1.000" = R$ 1.000,00), nao como
  // centavos de caixa eletronico. Texto que nao e valor e ignorado, com um aviso curto.
  const applyPasted = (text: string) => {
    const cents = parsePastedAmount(text);
    if (cents === null) {
      setPasteWarning("Não entendemos o que foi colado. Cole só o valor em reais, como 1.500,00.");
      return;
    }
    setPasteWarning(null);
    onCustomChange(cents);
  };

  // Resposta direta a acao do usuario: ao escolher "Outro valor", o campo ja recebe o foco.
  useEffect(() => {
    if (choice === CUSTOM_CHOICE && previous.current !== CUSTOM_CHOICE) customRef.current?.focus();
    previous.current = choice;
  }, [choice]);

  const minCents = school.minAmountCents;
  const maxCents = school.maxAmountCents;
  const customSelected = choice === CUSTOM_CHOICE;

  // Erro de valor livre: o foco vai para o campo (leitores de tela leem o erro via aria-describedby).
  useEffect(() => {
    if (error && customSelected) customRef.current?.focus();
  }, [error, customSelected]);
  const groupError = !customSelected ? error : null;
  const customError = customSelected ? error : null;

  return (
    <form
      method="post"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        onContinue();
      }}
    >
      <h2 id="amount-heading" ref={headingRef} tabIndex={-1} className="text-title text-ink outline-none">
        Quanto você quer contribuir?
      </h2>
      <p className="mt-2 max-w-[52ch] text-body text-ink-2">
        {school.allowCustomAmount
          ? `Escolha um dos valores sugeridos pela ${school.apmName} ou informe outro valor.`
          : `Escolha um dos valores sugeridos pela ${school.apmName}.`}{" "}
        O dinheiro vai direto para a conta da APM.
      </p>

      <div role="radiogroup" aria-labelledby="amount-heading" className="mt-6 space-y-3">
        {school.suggestedAmountsCents.map((cents) => (
          <ChoiceRow
            key={cents}
            value={suggestedChoice(cents)}
            checked={choice === suggestedChoice(cents)}
            title={formatBRL(cents)}
            description="Valor sugerido pela APM"
            trailing=""
            onSelect={onChoice}
          />
        ))}
        {school.allowCustomAmount && (
          <ChoiceRow
            value={CUSTOM_CHOICE}
            checked={customSelected}
            title="Outro valor"
            description={`Entre ${formatBRL(minCents)} e ${formatBRL(maxCents)}`}
            trailing=""
            onSelect={onChoice}
          />
        )}
      </div>

      {customSelected && (
        <div className="pop mt-4">
          <TextField
            label="Valor da contribuição"
            name="custom-amount"
            prefix="R$"
            inputMode="numeric"
            autoComplete="off"
            placeholder="0,00"
            inputRef={customRef}
            value={customCents === 0 ? "" : formatBRLNumber(customCents)}
            onChange={(e) => {
              setPasteWarning(null);
              onCustomChange(centsFromDigits(e.target.value));
            }}
            onPaste={(e) => {
              e.preventDefault();
              applyPasted(e.clipboardData.getData("text"));
            }}
            onDrop={(e) => {
              e.preventDefault();
              applyPasted(e.dataTransfer.getData("text"));
            }}
            hint={`Mínimo ${formatBRL(minCents)}, máximo ${formatBRL(maxCents)}.`}
            error={customError ?? undefined}
          />
          <div aria-live="polite">
            {pasteWarning && <p className="mt-1.5 text-sub font-medium text-warn">{pasteWarning}</p>}
          </div>
        </div>
      )}

      <div aria-live="polite">
        {groupError && (
          <p className="mt-3 text-sub font-medium text-bad" role="alert">
            {groupError}
          </p>
        )}
      </div>

      <StickyActions>
        <Button type="submit" className="flex-1 sm:min-w-48 sm:flex-none">
          Continuar
        </Button>
      </StickyActions>
    </form>
  );
}
