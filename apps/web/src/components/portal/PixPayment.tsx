"use client";

import { useEffect, useRef, useState } from "react";
import { Button, ButtonLink } from "@/components/ui/Button";
import { CheckIcon, ClockIcon, CopyIcon, InfoIcon, RefreshIcon } from "@/components/ui/icons";
import { api } from "@/lib/api";
import type { ApiResult, PixCharge } from "@/lib/api/types";
import { formatCountdown } from "@/lib/format";
import { useChargeStatus } from "@/lib/hooks/useChargeStatus";
import { useCountdown } from "@/lib/hooks/useCountdown";
import { formatBRL } from "@/lib/money";
import { pixStatusView } from "@/lib/status";
import { PaidStamp } from "./ReceiptCard";
import { QrPlaceholder } from "./QrPlaceholder";

type CopyState = "idle" | "copied" | "manual";

interface PixPaymentProps {
  slug: string;
  charge: PixCharge;
  /** Pede um novo Pix (a camada de dados cria outro). */
  onNewCharge: () => void;
  onRestart: () => void;
  /** Injetavel para teste. Padrao: consulta a camada de dados. */
  fetchCharge?: () => Promise<ApiResult<PixCharge>>;
  pollMs?: number;
}

/**
 * Passo Pix. REGRA: este componente nunca marca nada como pago. Ele consulta a camada de dados
 * (polling) e renderiza o status que ela devolve. "Pago" so aparece se status === "PAID".
 */
export function PixPayment({ slug, charge: initial, onNewCharge, onRestart, fetchCharge, pollMs }: PixPaymentProps) {
  const { charge, status, connectionIssue, notFound } = useChargeStatus({
    initial,
    fetchCharge: fetchCharge ?? (() => api.contributions.getCharge(slug, initial.token)),
    intervalMs: pollMs,
  });
  const view = pixStatusView(status);
  const waiting = status === "PENDING" || status === null;
  const remaining = useCountdown(charge.expiresAt, waiting);

  const [copy, setCopy] = useState<CopyState>("idle");
  const codeRef = useRef<HTMLTextAreaElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(copyTimer.current), []);

  // Ao abrir o passo Pix, o foco vai para o titulo.
  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  // Quando o status final chega, o foco vai para o titulo (leitores de tela anunciam).
  const finalReached = view.final;
  useEffect(() => {
    if (finalReached) headingRef.current?.focus();
  }, [finalReached]);

  async function copyCode() {
    clearTimeout(copyTimer.current);
    try {
      await navigator.clipboard.writeText(charge.payload);
      setCopy("copied");
    } catch {
      codeRef.current?.select();
      setCopy("manual");
    }
    copyTimer.current = setTimeout(() => setCopy("idle"), 4000);
  }

  if (notFound) {
    return (
      <div className="step-in rounded-[var(--r-lg)] bg-surface p-6 shadow-[0_0_0_1px_var(--line)]">
        <h2 ref={headingRef} tabIndex={-1} className="text-heading text-ink outline-none">
          Não encontramos este Pix
        </h2>
        <p className="mt-2 text-body text-ink-2">A cobrança não existe mais. Comece uma nova contribuição.</p>
        <Button className="mt-5 w-full sm:w-auto" onClick={onRestart}>
          Começar de novo
        </Button>
      </div>
    );
  }

  if (status === "PAID") {
    return (
      <div className="step-in rounded-[var(--r-lg)] bg-surface p-6 text-center shadow-[0_0_0_1px_var(--line)] sm:p-10">
        <div className="relative mx-auto grid size-32 place-items-center">
          <PaidStamp animate size={128} />
        </div>
        <h2 ref={headingRef} tabIndex={-1} className="mt-4 text-title text-ink outline-none">
          Pagamento confirmado
        </h2>
        <p className="mt-2 text-body text-ink-2">
          Recebemos a contribuição de{" "}
          <strong className="font-semibold tabular-nums text-ink">{formatBRL(charge.amountCents)}</strong>. Obrigado por
          apoiar a escola.
        </p>
        <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:justify-center">
          <ButtonLink href={`/apm/${slug}/pedido/${charge.token}`}>Ver comprovante</ButtonLink>
          <Button variant="secondary" onClick={onRestart}>
            Fazer outra contribuição
          </Button>
        </div>
        <p role="status" className="sr-only-live">
          {view.description}
        </p>
      </div>
    );
  }

  if (status === "EXPIRED" || status === "CANCELLED") {
    return (
      <div className="step-in rounded-[var(--r-lg)] bg-surface p-6 shadow-[0_0_0_1px_var(--line)] sm:p-8">
        <h2 ref={headingRef} tabIndex={-1} className="text-heading text-ink outline-none">
          {status === "EXPIRED" ? "Este Pix expirou" : "Este Pix foi cancelado"}
        </h2>
        <p role="status" className="mt-2 text-body text-ink-2">
          {view.description} Gere um novo Pix para continuar.
        </p>
        <div className="mt-6 flex flex-col gap-3 sm:flex-row">
          <Button onClick={onNewCharge}>
            <RefreshIcon size={20} />
            Gerar novo Pix
          </Button>
          <Button variant="secondary" onClick={onRestart}>
            Começar de novo
          </Button>
        </div>
      </div>
    );
  }

  const timeUp = remaining <= 0;
  return (
    <div className="step-in">
      <div className="rounded-[var(--r-lg)] bg-surface p-5 shadow-[0_0_0_1px_var(--line)] sm:p-8">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h2 ref={headingRef} tabIndex={-1} className="text-heading text-ink outline-none">
            Pague com Pix
          </h2>
          <p className="text-heading tabular-nums text-ink">{formatBRL(charge.amountCents)}</p>
        </div>

        <p className="mt-1 text-sub text-ink-2">
          Abra o app do seu banco e escolha pagar com Pix. Use o QR Code ou o código copia e cola.
        </p>

        <div className="mt-6">
          <QrPlaceholder payload={charge.payload} />
          <p className="mx-auto mt-3 max-w-[34ch] text-center text-foot text-ink-2">
            Pix de exemplo para demonstração. Nenhum pagamento real é feito e nenhum app de banco lê este código.
          </p>
        </div>

        <div className="mt-6">
          <label htmlFor="pix-code" className="mb-1.5 block text-sub font-semibold text-ink">
            Pix copia e cola
          </label>
          <textarea
            id="pix-code"
            ref={codeRef}
            readOnly
            rows={3}
            value={charge.payload}
            onFocus={(e) => e.currentTarget.select()}
            className="block w-full resize-none rounded-[14px] border border-field-border bg-field px-4 py-3 font-mono text-foot leading-relaxed text-ink [overflow-wrap:anywhere]"
          />
          <Button
            variant={copy === "copied" ? "secondary" : "primary"}
            className="mt-3 w-full"
            onClick={copyCode}
            aria-describedby="pix-copy-status"
          >
            {copy === "copied" ? <CheckIcon size={20} className="pop" /> : <CopyIcon size={20} />}
            {copy === "copied" ? "Código copiado" : "Copiar código"}
          </Button>
          <p id="pix-copy-status" role="status" className="mt-2 min-h-5 text-foot text-ink-2">
            {copy === "copied" && "Código copiado. Cole no app do seu banco."}
            {copy === "manual" && "O navegador não permitiu copiar. O código está selecionado: use Ctrl+C ou Cmd+C."}
          </p>
        </div>
      </div>

      <div className="mt-4 rounded-[var(--r-lg)] bg-surface p-5 shadow-[0_0_0_1px_var(--line)]">
        <div className="flex items-center justify-between gap-4">
          <p className="flex items-center gap-2 text-sub font-semibold text-ink">
            <ClockIcon size={18} className="text-ink-2" />
            {timeUp ? "Prazo encerrado" : "Expira em"}
          </p>
          <p
            role="timer"
            aria-label={timeUp ? "Prazo encerrado" : `Tempo restante ${formatCountdown(remaining)}`}
            className="text-heading tabular-nums text-ink"
          >
            {formatCountdown(remaining)}
          </p>
        </div>
        <div className="mt-4 flex items-start gap-3 border-t border-line pt-4">
          <span aria-hidden="true" className="pulse-dot mt-2 size-2.5 shrink-0 rounded-full bg-info" />
          <div role="status" aria-live="polite" className="text-sub text-ink">
            <p className="font-semibold">{view.label}</p>
            <p className="text-ink-2">
              {timeUp ? "Conferindo com o banco se o pagamento chegou…" : view.description}
            </p>
            {connectionIssue && (
              <p className="mt-1 flex items-start gap-1.5 text-warn">
                <InfoIcon size={16} className="mt-0.5 shrink-0" />
                Sem conexão para atualizar o status. Tentando de novo.
              </p>
            )}
          </div>
        </div>
      </div>

      <p className="mx-auto mt-4 max-w-[44ch] text-center text-foot text-ink-2">
        Esta tela atualiza sozinha quando o banco confirmar. Se precisar mudar algo, comece de novo: o Pix atual expira
        sozinho.
      </p>
      <div className="mt-1 text-center">
        <Button variant="plain" size="md" onClick={onRestart}>
          Começar de novo
        </Button>
      </div>
    </div>
  );
}
