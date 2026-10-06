"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button, ButtonLink } from "@/components/ui/Button";
import { CheckIcon, ClockIcon, CopyIcon, InfoIcon, RefreshIcon } from "@/components/ui/icons";
import { api } from "@/lib/api";
import { sandboxTxid } from "@/lib/api/public";
import type { ApiResult, ContributionState } from "@/lib/api/types";
import { formatCountdown } from "@/lib/format";
import { useContributionState } from "@/lib/hooks/useContributionState";
import { useCountdown } from "@/lib/hooks/useCountdown";
import { formatBRL } from "@/lib/money";
import { describeRenewError } from "@/lib/public-messages";
import { PaidStamp } from "./ReceiptCard";
import { QrCode } from "./QrCode";

type CopyState = "idle" | "copied" | "manual";

interface PixPaymentProps {
  slug: string;
  /** Token opaco da contribuicao: segredo da familia, so em memoria e na URL do comprovante. */
  token: string;
  initial: ContributionState;
  onRestart: () => void;
  /** Injetaveis para teste. Padrao: a API real. */
  fetchState?: () => Promise<ApiResult<ContributionState>>;
  pollMs?: number;
}

/**
 * Passo Pix. REGRA: este componente nunca marca nada como pago. Ele consulta a API (polling de
 * GET .../charge) e renderiza o que ela devolve. "Pago" so aparece se a CONTRIBUICAO vier PAID.
 */
export function PixPayment({ slug, token, initial, onRestart, fetchState, pollMs }: PixPaymentProps) {
  const { state, phase, connectionIssue, notFound, replace, refresh } = useContributionState({
    initial,
    fetchState: fetchState ?? (() => api.public.contribution(slug, token)),
    intervalMs: pollMs,
  });
  const charge = state.charge;
  const payload = charge?.emvPayload ?? "";
  const waiting = phase === "waiting";
  const remaining = useCountdown(charge?.expiresAt ?? new Date(0).toISOString(), waiting);
  const txid = sandboxTxid(charge?.emvPayload ?? null);

  const [renewing, setRenewing] = useState(false);
  const [renewError, setRenewError] = useState<string | null>(null);
  const [simulating, setSimulating] = useState(false);
  const [simulateMessage, setSimulateMessage] = useState<string | null>(null);

  const [copy, setCopy] = useState<CopyState>("idle");
  const codeRef = useRef<HTMLTextAreaElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(copyTimer.current), []);

  // A caixa do codigo cresce com o texto: em telas estreitas o payload ocupa mais linhas e nenhuma
  // pode ficar escondida (a quebra e entre palavras, nunca no meio de VALOR).
  const hasCode = waiting;
  useLayoutEffect(() => {
    const el = codeRef.current;
    if (!el || !hasCode) return;
    const fit = () => {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight + (el.offsetHeight - el.clientHeight)}px`;
    };
    fit();
    if (typeof ResizeObserver === "undefined" || !el.parentElement) return;
    const observer = new ResizeObserver(fit);
    observer.observe(el.parentElement); // a largura do contêiner muda a quebra de linha
    return () => observer.disconnect();
  }, [payload, hasCode]);

  // Ao abrir o passo Pix, o foco vai para o titulo.
  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  // Quando o status final chega, o foco vai para o titulo (leitores de tela anunciam).
  useEffect(() => {
    if (phase !== "waiting") headingRef.current?.focus();
  }, [phase]);

  async function copyCode() {
    clearTimeout(copyTimer.current);
    try {
      await navigator.clipboard.writeText(payload);
      setCopy("copied");
    } catch {
      codeRef.current?.select();
      setCopy("manual");
    }
    copyTimer.current = setTimeout(() => setCopy("idle"), 4000);
  }

  async function renew() {
    if (renewing) return;
    setRenewing(true);
    setRenewError(null);
    const result = await api.public.renewCharge(slug, token);
    setRenewing(false);
    if (result.ok) replace(result.data);
    else setRenewError(describeRenewError(result.error));
  }

  // Somente desenvolvimento: faz o banco de mentira pagar. NAO confirma nada aqui: a tela so muda
  // quando a API, na proxima consulta, devolver a contribuicao PAID.
  async function simulatePayment() {
    if (!txid || simulating) return;
    setSimulating(true);
    setSimulateMessage(null);
    const result = await api.public.sandboxPay(txid);
    setSimulating(false);
    if (result.ok) refresh();
    else setSimulateMessage("Não foi possível simular o pagamento (isso só existe em desenvolvimento).");
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

  if (phase === "paid") {
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
          <strong className="font-semibold tabular-nums text-ink">{formatBRL(state.amountCents)}</strong>. Obrigado por
          apoiar a escola.
        </p>
        <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:justify-center">
          <ButtonLink href={`/escola/${slug}/pedido/${token}`}>Ver comprovante</ButtonLink>
          <Button variant="secondary" onClick={onRestart}>
            Fazer outra contribuição
          </Button>
        </div>
        <p role="status" className="sr-only-live">
          Pagamento confirmado.
        </p>
      </div>
    );
  }

  if (phase === "review") {
    return (
      <div className="step-in rounded-[var(--r-lg)] bg-surface p-6 shadow-[0_0_0_1px_var(--line)] sm:p-8">
        <h2 ref={headingRef} tabIndex={-1} className="text-heading text-ink outline-none">
          Pagamento em análise pela escola
        </h2>
        <p role="status" className="mt-2 max-w-[52ch] text-body text-ink-2">
          Recebemos um pagamento, mas a escola precisa conferi-lo antes de confirmar a contribuição. Por enquanto ela{" "}
          <strong className="font-semibold text-ink">não está confirmada</strong> e não há comprovante. Se a escola
          confirmar, esta página atualiza sozinha.
        </p>
        {connectionIssue && (
          <p className="mt-3 flex items-start gap-1.5 text-sub text-warn">
            <InfoIcon size={16} className="mt-0.5 shrink-0" />
            Sem conexão para atualizar o status. Tentando de novo.
          </p>
        )}
        <div className="mt-6">
          <Button variant="secondary" onClick={onRestart}>
            Começar de novo
          </Button>
        </div>
      </div>
    );
  }

  if (phase === "closed") {
    return (
      <div className="step-in rounded-[var(--r-lg)] bg-surface p-6 shadow-[0_0_0_1px_var(--line)] sm:p-8">
        <h2 ref={headingRef} tabIndex={-1} className="text-heading text-ink outline-none">
          Esta contribuição foi encerrada
        </h2>
        <p role="status" className="mt-2 text-body text-ink-2">
          Ela expirou ou foi cancelada e nenhum valor foi cobrado. Comece uma nova contribuição para continuar.
        </p>
        <Button className="mt-6" onClick={onRestart}>
          Começar de novo
        </Button>
      </div>
    );
  }

  if (phase === "expired") {
    return (
      <div className="step-in rounded-[var(--r-lg)] bg-surface p-6 shadow-[0_0_0_1px_var(--line)] sm:p-8">
        <h2 ref={headingRef} tabIndex={-1} className="text-heading text-ink outline-none">
          Este QR expirou
        </h2>
        <p role="status" className="mt-2 text-body text-ink-2">
          O Pix expirou e nenhum valor foi cobrado. Gere um novo QR para continuar.
        </p>
        <div aria-live="polite">
          {renewError && (
            <p role="alert" className="mt-4 rounded-[var(--r-md)] bg-bad-soft px-4 py-3 text-sub font-medium text-bad">
              {renewError}
            </p>
          )}
        </div>
        <div className="mt-6 flex flex-col gap-3 sm:flex-row">
          <Button onClick={renew} disabled={renewing} aria-busy={renewing}>
            <RefreshIcon size={20} />
            {renewing ? "Gerando…" : "Gerar novo QR"}
          </Button>
          <Button variant="secondary" onClick={onRestart} disabled={renewing}>
            Começar de novo
          </Button>
        </div>
      </div>
    );
  }

  const timeUp = remaining <= 0;
  return (
    <div>
      <div className="rounded-[var(--r-lg)] bg-surface p-5 shadow-[0_0_0_1px_var(--line)] sm:p-8">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h2 ref={headingRef} tabIndex={-1} className="text-heading text-ink outline-none">
            Pague com Pix
          </h2>
          <p className="text-heading tabular-nums text-ink">{formatBRL(state.amountCents)}</p>
        </div>

        <p className="mt-1 text-sub text-ink-2">
          Abra o app do seu banco e escolha pagar com Pix. Use o QR Code ou o código copia e cola.
        </p>

        <div className="mt-6">
          <QrCode payload={payload} />
          {txid && (
            <p className="mx-auto mt-3 max-w-[34ch] text-center text-foot text-ink-2">
              Pix de teste (sandbox). Nenhum pagamento real é feito e nenhum app de banco lê este código.
            </p>
          )}
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
            value={payload}
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

        {txid && (
          <div className="mt-4 border-t border-dashed border-line pt-4">
            <Button variant="secondary" size="md" onClick={simulatePayment} disabled={simulating} aria-busy={simulating}>
              {simulating ? "Simulando…" : "Simular pagamento (somente desenvolvimento)"}
            </Button>
            <div aria-live="polite">
              {simulateMessage && <p className="mt-2 text-sub font-medium text-bad">{simulateMessage}</p>}
            </div>
          </div>
        )}
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
            <p className="font-semibold">Aguardando pagamento</p>
            <p className="text-ink-2">
              {timeUp ? "Conferindo com o banco se o pagamento chegou…" : "Aguardando a confirmação do pagamento."}
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
