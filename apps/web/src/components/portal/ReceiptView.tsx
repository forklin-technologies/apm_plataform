"use client";

import { notFound } from "next/navigation";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button, ButtonLink } from "@/components/ui/Button";
import { CheckIcon, CopyIcon, LockIcon } from "@/components/ui/icons";
import { api } from "@/lib/api";
import type { Receipt } from "@/lib/api/types";
import { ReceiptCard } from "./ReceiptCard";

type ViewState =
  | { kind: "checking" }
  | { kind: "receipt"; receipt: Receipt }
  | { kind: "unconfirmed" }
  | { kind: "not-found" }
  | { kind: "error" };

/**
 * Comprovante. O servidor so entrega o que a camada de dados confirmou la (o link de exemplo); para
 * qualquer outro token o HTML e NEUTRO ("Conferindo seu comprovante") e quem responde e a camada de
 * dados, no navegador. Nunca se afirma "Pago" nem se mostram dados de pessoa sem essa resposta.
 */
export function ReceiptView({
  slug,
  token,
  initial,
}: {
  slug: string;
  token: string;
  initial: Receipt | null;
}) {
  const [view, setView] = useState<ViewState>(initial ? { kind: "receipt", receipt: initial } : { kind: "checking" });
  const [attempt, setAttempt] = useState(0);
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useLayoutEffect(() => {
    let cancelled = false;
    void api.contributions.getReceipt(slug, token).then((result) => {
      if (cancelled) return;
      if (result.ok) setView({ kind: "receipt", receipt: result.data });
      else if (result.error.kind === "not-found") setView({ kind: "not-found" });
      else if (result.error.status === 409) setView({ kind: "unconfirmed" });
      else setView({ kind: "error" });
    });
    return () => {
      cancelled = true;
      clearTimeout(timer.current);
    };
  }, [slug, token, attempt]);

  // Dado de crianca: depois de MOSTRAR o comprovante, apaga do navegador responsavel, aluno e turma.
  const shown = view.kind === "receipt";
  useEffect(() => {
    if (shown) void api.contributions.clearPersonalData(slug, token);
  }, [shown, slug, token]);

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
        <p className="mt-3 text-body text-ink-2">Houve uma falha ao buscar o comprovante. Tente de novo em instantes.</p>
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
          O comprovante só existe depois que o banco confirma o Pix. Se você acabou de pagar, aguarde um instante e abra
          o link de novo.
        </p>
        <ButtonLink href={`/apm/${slug}`} className="mt-7 w-full sm:w-auto">
          Voltar para a escola
        </ButtonLink>
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
          <ButtonLink href={`/apm/${slug}`} variant="plain">
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
