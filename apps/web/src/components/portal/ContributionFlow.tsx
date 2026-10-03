"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ContributionAmount, PixCharge, PublicSchool } from "@/lib/api/types";
import {
  validateAmount,
  validateIdentification,
  visibleFields,
  type IdentificationErrors,
  type IdentificationField,
  type IdentificationValues,
} from "@/lib/validation";
import { AmountStep, CUSTOM_CHOICE } from "./AmountStep";
import { StepProgress, SummaryCard, type Step } from "./FlowChrome";
import { IdentificationStep } from "./IdentificationStep";
import { PixPayment } from "./PixPayment";
import { ReviewStep } from "./ReviewStep";

/**
 * Fluxo publico de contribuicao (sem login, ADR-010). A validacao aqui e de UX; o backend
 * revalida tudo e e quem confirma o pagamento.
 */
export function ContributionFlow({ school }: { school: PublicSchool }) {
  const fields = visibleFields(school.identification);
  const steps: Step[] = fields.length > 0 ? ["amount", "identification", "review", "pix"] : ["amount", "review", "pix"];

  const [step, setStepState] = useState<Step>("amount");
  // A entrada animada so acontece depois de uma acao do usuario (nunca no carregamento da pagina).
  const [navigated, setNavigated] = useState(false);
  const setStep = (next: Step) => {
    setNavigated(true);
    setStepState(next);
  };
  const [choice, setChoice] = useState<string | null>(null);
  const [customCents, setCustomCents] = useState(0);
  const [amountError, setAmountError] = useState<string | null>(null);
  const [values, setValues] = useState<IdentificationValues>({});
  const [errors, setErrors] = useState<IdentificationErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [charge, setCharge] = useState<PixCharge | null>(null);

  const headingRef = useRef<HTMLHeadingElement>(null);
  const mounted = useRef(false);

  // Ao trocar de etapa, o foco vai para o titulo da etapa (teclado e leitores de tela).
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    headingRef.current?.focus();
  }, [step]);

  const quota = school.quotas.find((q) => q.id === choice);
  const amountCents = choice === CUSTOM_CHOICE ? customCents : (quota?.amountCents ?? null);
  const description =
    choice === CUSTOM_CHOICE
      ? customCents > 0
        ? "Valor livre"
        : null
      : (quota?.name ?? null);

  function onChoice(next: string) {
    setChoice(next);
    setAmountError(null);
  }

  function continueFromAmount() {
    if (choice === null) {
      setAmountError("Escolha uma cota ou informe outro valor.");
      return;
    }
    if (choice === CUSTOM_CHOICE) {
      const check = validateAmount(customCents, school.customAmount);
      if (!check.ok) {
        setAmountError(check.message);
        return;
      }
    }
    setAmountError(null);
    setStep(fields.length > 0 ? "identification" : "review");
  }

  function continueFromIdentification(focusField: (field: IdentificationField) => void) {
    const result = validateIdentification(values, school.identification);
    setErrors(result.errors);
    if (!result.ok) {
      const first = fields.find((f) => result.errors[f]);
      if (first) focusField(first);
      return;
    }
    setValues(result.cleaned);
    setStep("review");
  }

  function buildAmount(): ContributionAmount | null {
    if (choice === null) return null;
    return choice === CUSTOM_CHOICE ? { kind: "CUSTOM", cents: customCents } : { kind: "QUOTA", quotaId: choice };
  }

  async function createCharge() {
    const amount = buildAmount();
    if (!amount) return;
    setSubmitting(true);
    setSubmitError(null);
    const result = await api.contributions.create({ slug: school.slug, amount, identification: values });
    setSubmitting(false);
    if (!result.ok) {
      setSubmitError(
        result.error.status === 422
          ? "Algum dado não foi aceito pela escola. Volte e confira o valor e os campos."
          : "Não foi possível gerar o Pix agora. Tente de novo em instantes.",
      );
      return;
    }
    setCharge(result.data.charge);
    setStep("pix");
  }

  function restart() {
    setStep("amount");
    setChoice(null);
    setCustomCents(0);
    setValues({});
    setErrors({});
    setAmountError(null);
    setSubmitError(null);
    setCharge(null);
  }

  return (
    <div>
      <h1 className="text-title text-ink sm:text-[2.25rem]">Contribuir com a APM</h1>
      <p className="mb-8 mt-2 text-body text-ink-2">Pelo celular, em poucos passos e sem criar conta.</p>

      <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_21rem] lg:items-start lg:gap-14">
        <div className="min-w-0">
          <StepProgress steps={steps} current={step} />

          <div key={step} className={navigated ? "step-in" : undefined}>
            {step === "amount" && (
              <AmountStep
                school={school}
                choice={choice}
                customCents={customCents}
                error={amountError}
                headingRef={headingRef}
                onChoice={onChoice}
                onCustomChange={(cents) => {
                  setCustomCents(cents);
                  setAmountError(null);
                }}
                onContinue={continueFromAmount}
              />
            )}

            {step === "identification" && (
              <IdentificationStep
                school={school}
                values={values}
                errors={errors}
                headingRef={headingRef}
                onChange={(field, value) => {
                  setValues((prev) => ({ ...prev, [field]: value }));
                  setErrors((prev) => ({ ...prev, [field]: undefined }));
                }}
                onBack={() => setStep("amount")}
                onContinue={continueFromIdentification}
              />
            )}

            {step === "review" && amountCents !== null && description !== null && (
              <ReviewStep
                schoolName={school.name}
                description={description}
                amountCents={amountCents}
                identification={values}
                submitting={submitting}
                error={submitError}
                headingRef={headingRef}
                onEditAmount={() => setStep("amount")}
                onEditIdentification={fields.length > 0 ? () => setStep("identification") : null}
                onBack={() => setStep(fields.length > 0 ? "identification" : "amount")}
                onSubmit={createCharge}
              />
            )}

            {step === "pix" && charge && (
              <PixPayment
                key={charge.token}
                slug={school.slug}
                charge={charge}
                onNewCharge={createCharge}
                onRestart={restart}
              />
            )}
          </div>
        </div>

        <div className="hidden lg:sticky lg:top-24 lg:block">
          <SummaryCard
            schoolName={school.name}
            amountCents={amountCents}
            description={description}
            identification={step === "amount" ? {} : values}
          >
            <p className="mt-5 border-t border-line pt-4 text-foot text-ink-2">
              Sem login. O comprovante chega por um link só seu, e a escola só vê os dados que você informar.
            </p>
          </SummaryCard>
        </div>
      </div>
    </div>
  );
}
