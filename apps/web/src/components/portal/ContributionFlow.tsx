"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { CreatedContribution, PublicSchool } from "@/lib/api/types";
import { describeContributionError } from "@/lib/public-messages";
import {
  validateAmount,
  validateIdentification,
  visibleFields,
  type IdentificationErrors,
  type IdentificationField,
  type IdentificationValues,
} from "@/lib/validation";
import { AmountStep, CUSTOM_CHOICE, suggestedCents } from "./AmountStep";
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
  // O token (segredo da familia) so vive aqui, em memoria: nunca em storage nem em URL ate o comprovante.
  const [created, setCreated] = useState<CreatedContribution | null>(null);
  // A Idempotency-Key e tao secreta quanto o token: uma por TENTATIVA (mesmo valor e mesmos dados),
  // reaproveitada se o POST for repetido por falha de rede e trocada quando o usuario muda algo.
  const attempt = useRef<{ key: string; signature: string } | null>(null);

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

  const suggested = suggestedCents(choice);
  const amountCents = choice === CUSTOM_CHOICE ? customCents : suggested;
  const description =
    choice === CUSTOM_CHOICE ? (customCents > 0 ? "Valor livre" : null) : suggested !== null ? "Valor sugerido pela APM" : null;

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
      const check = validateAmount(customCents, { minCents: school.minAmountCents, maxCents: school.maxAmountCents });
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

  async function createCharge() {
    if (amountCents === null || amountCents <= 0) return;
    const input = { amountCents, identification: values };
    const signature = JSON.stringify(input);
    if (attempt.current?.signature !== signature) attempt.current = { key: crypto.randomUUID(), signature };
    setSubmitting(true);
    setSubmitError(null);
    const result = await api.public.createContribution(school.slug, input, attempt.current.key);
    setSubmitting(false);
    if (!result.ok) {
      const problems = describeContributionError(result.error);
      if (result.error.code === "idempotency_key_reused") attempt.current = null;
      const fieldProblems = Object.keys(problems.fields).filter((f) => fields.includes(f as IdentificationField));
      if (fieldProblems.length > 0) {
        setErrors(problems.fields);
        setStep("identification");
        return;
      }
      if (problems.amount) {
        setAmountError(problems.amount);
        setStep("amount");
        return;
      }
      setSubmitError(problems.general ?? "Não foi possível gerar o Pix agora. Tente de novo em instantes.");
      return;
    }
    attempt.current = null; // contribuicao criada: a chave nao serve mais para nada
    setCreated(result.data);
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
    setCreated(null);
    attempt.current = null;
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

            {step === "pix" && created && (
              <PixPayment
                key={created.token}
                slug={school.slug}
                token={created.token}
                initial={created}
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
