"use client";

import { notFound } from "next/navigation";
import { useLayoutEffect, useRef, useState } from "react";
import { Button, ButtonLink } from "@/components/ui/Button";
import { CheckIcon, CopyIcon, LockIcon } from "@/components/ui/icons";
import { api } from "@/lib/api";
import type { Receipt } from "@/lib/api/types";
import { formatWait } from "@/lib/auth-messages";
import { ReceiptCard } from "./ReceiptCard";

type ViewState =
  | { kind: "checking" }
  | { kind: "receipt"; receipt: Receipt }
  | { kind: "unconfirmed" }
  | { kind: "not-found" }
  | { kind: "error"; message: string };

/**
 * Comprovante. O servidor entrega a pagina ja com o que a API respondeu (200 pago, 409 ainda nao, 404
 * vira a 404 do site); sem resposta definitiva, o HTML e NEUTRO ("Conferindo seu comprovante") e o
 * navegador consulta. Nunca se afirma "Pago" sem o 200 da API. O token so existe na URL desta pagina e
 * na memoria dela: nada em storage.
 */
export function ReceiptView({
  slug,
  token,
  initial,
}: {
  slug: string;
  token: string;
  initial: { receipt: Receipt } | { unconfirmed: true } | null;
}) {
  const [view, setView] = useState<ViewState>(
    initial === null
      ? { kind: "checking" }
      : "receipt" in initial
        ? { kind: "receipt", receipt: initial.receipt }
        : { kind: "unconfirmed" },
  );
  const [attempt, setAttempt] = useState(0);
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useLayoutEffect(() => {
    // O servidor ja trouxe a resposta definitiva da API (200 ou 409): so consulta de novo se a pessoa pedir.
    if (attempt === 0 && initial !== null) return;
    let cancelled = false;
    void api.public.receipt(slug, token).then((result) => {
      if (cancelled) return;
      if (result.ok) setView({ kind: "receipt", receipt: result.data });
      else if (result.error.status === 404) setView({ kind: "not-found" });
      else if (result.error.status === 409) setView({ kind: "unconfirmed" });
      else if (result.error.code === "rate_limited") {
        setView({ kind: "error", message: `Muitas consultas seguidas. Aguarde ${formatWait(result.error.retryAfterSeconds)} e tente de novo.` });
      } else setView({ kind: "error", message: "Houve uma falha ao buscar o comprovante. Tente de novo em instantes." });
    });
    return () => {
      cancelled = true;
      clearTimeout(timer.current);
    };
    // `initial` so vale na primeira consulta.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, token, attempt]);

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 4000);
    } catch {
      setCopied(false);
    }
  }

  // A mesma 404 estilizada para "nunca existiu" e "ainda nao existe" (sem enumeracao, ADR-010).
  if (view.kind === "not-found") notFound();

  if (view.kind === "checking") {
    return (
      <div role="status" aria-live="polite" aria-busy="true" className="max-w-xl">
        <h1 className="text-title text-ink">Conferindo seu comprovante</h1>
        <p className="mt-3 text-body text-ink-2">Estamos conferindo este link. Isso leva só um instante.</p>
        <div aria-hidden="true" className="mt-8 w-full max-w-[26rem] rounded-[var(--r-lg)] bg-surface p-6 shadow-[0_0_0_1px_var(--line)]">
          <div className="skeleton h-4 w-40" />
          <div className="skeleton mt-6 h-10 w-48" />
          <div className="skeleton mt-8 h-4 w-full" />
          <div className="skeleton mt-3 h-4 w-5/6" />
          <div className="skeleton mt-3 h-4 w-2/3" />
        </div>
      </div>
    );
  }

  if (view.kind === "error") {
    return (
      <div className="max-w-xl">
        <h1 className="text-title text-ink">Não conseguimos conferir agora</h1>
        <p role="alert" className="mt-3 text-body text-ink-2">{view.message}</p>
        <Button className="mt-7 w-full sm:w-auto" onClick={() => { setView({ kind: "checking" }); setAttempt((n) => n + 1); }}>
          Tentar de novo
        </Button>
      </div>
    );
  }

  if (view.kind === "unconfirmed") {
    return (
      <div className="max-w-xl">
        <h1 className="text-title text-ink">Este pagamento ainda não foi confirmado</h1>
        <p className="mt-3 text-body text-ink-2">
          O comprovante só existe depois que o banco confirma o Pix. Se você acabou de pagar, aguarde um instante e
          atualize esta página. Se o pagamento estiver em análise pela escola, ele aparece aqui quando for confirmado.
        </p>
        <div className="mt-7 flex flex-col gap-3 sm:flex-row">
          <Button onClick={() => { setView({ kind: "checking" }); setAttempt((n) => n + 1); }}>Atualizar</Button>
          <ButtonLink href={`/escola/${slug}`} variant="secondary">
            Voltar para a escola
          </ButtonLink>
        </div>
      </div>
    );
  }

  const confirmed = view.receipt.status === "PAID";
  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)] lg:items-start lg:gap-16">
      <div className="mx-auto w-full max-w-[26rem] lg:mx-0">
        <ReceiptCard receipt={view.receipt} />
      </div>
      <div className="max-w-xl">
        <h1 className="text-title text-ink sm:text-[2.25rem]">
          {confirmed ? "Contribuição confirmada" : "Comprovante da contribuição"}
        </h1>
        <p className="mt-3 text-body text-ink-2">
          {confirmed
            ? "Obrigado por apoiar a escola. Este é o seu comprovante: guarde o link, porque é ele que dá acesso a esta página."
            : "Acompanhe a situação do pagamento por este link."}
        </p>
        <p className="mt-4 flex items-start gap-2.5 text-sub text-ink-2">
          <LockIcon size={18} className="mt-0.5 shrink-0" />
          Não pedimos login. Quem tem o link vê este comprovante, então compartilhe com cuidado.
        </p>
        <div className="mt-7 flex flex-col gap-3 sm:flex-row">
          <Button variant="secondary" onClick={copyLink} aria-describedby="receipt-copy-status" aria-label={copied ? undefined : "Copiar link do comprovante"}>
            {copied ? <CheckIcon size={20} className="pop" /> : <CopyIcon size={20} />}
            {copied ? "Link copiado" : "Copiar link"}
          </Button>
          <ButtonLink href={`/escola/${slug}`} variant="plain">
            Fazer outra contribuição
          </ButtonLink>
        </div>
        <p id="receipt-copy-status" role="status" className="mt-2 min-h-5 text-foot text-ink-2">
          {copied && "Link copiado para a área de transferência."}
        </p>
      </div>
    </div>
  );
}
