"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiResult, ContributionState } from "@/lib/api/types";
import { paymentPhase, type PaymentPhase } from "@/lib/status";

export interface UseContributionStateOptions {
  initial: ContributionState;
  /** Consulta o estado na API. O hook NUNCA calcula status: so repassa o que chega. */
  fetchState: () => Promise<ApiResult<ContributionState>>;
  intervalMs?: number;
  maxIntervalMs?: number;
}

export interface ContributionStateView {
  state: ContributionState;
  phase: PaymentPhase;
  /** True enquanto a ultima consulta falhou (rede, 429, 5xx) e o hook continua tentando. */
  connectionIssue: boolean;
  /** True se a contribuicao nao existe mais (404). */
  notFound: boolean;
  /** Troca o estado (ex.: depois de gerar um novo QR) e volta a consultar. */
  replace: (next: ContributionState) => void;
  /** Consulta agora (ex.: depois de simular o pagamento no sandbox). */
  refresh: () => void;
}

const MAX_RETRY_AFTER_MS = 60_000;

/**
 * Polling de GET .../charge a cada 2 s, com recuo ate 10 s quando a consulta falha (e respeitando o
 * Retry-After de um 429). Para quando a fase e final para a tela: paga, encerrada ou QR expirado.
 * Em analise, continua no ritmo mais lento: a escola pode decidir depois.
 */
export function useContributionState({
  initial,
  fetchState,
  intervalMs = 2000,
  maxIntervalMs = 10_000,
}: UseContributionStateOptions): ContributionStateView {
  const [state, setState] = useState<ContributionState>(initial);
  const [connectionIssue, setConnectionIssue] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [generation, setGeneration] = useState(0);
  const fetchRef = useRef(fetchState);
  const firstDelayRef = useRef<number | null>(null);

  useEffect(() => {
    fetchRef.current = fetchState;
  }, [fetchState]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;

    function schedule(wait: number) {
      timer = setTimeout(poll, wait);
    }

    async function poll() {
      const result = await fetchRef.current();
      if (cancelled) return;
      if (result.ok) {
        failures = 0;
        setConnectionIssue(false);
        setState(result.data);
        const phase = paymentPhase(result.data);
        if (phase === "paid" || phase === "closed" || phase === "expired") return;
        schedule(phase === "review" ? maxIntervalMs : intervalMs);
        return;
      }
      if (result.error.status === 404) {
        setNotFound(true);
        return;
      }
      failures += 1;
      setConnectionIssue(true);
      let wait = Math.min(intervalMs * 2 ** Math.max(0, failures - 1), maxIntervalMs);
      if (result.error.retryAfterSeconds) wait = Math.max(wait, Math.min(result.error.retryAfterSeconds * 1000, MAX_RETRY_AFTER_MS));
      schedule(wait);
    }

    const initialPhase = paymentPhase(state);
    // Fase final ao montar (ex.: o servidor ja entregou PAID): nao ha o que consultar.
    const firstDelay = firstDelayRef.current ?? intervalMs;
    firstDelayRef.current = null;
    if (initialPhase !== "paid" && initialPhase !== "closed" && initialPhase !== "expired") schedule(firstDelay);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // `state` entra so pelo estado inicial de cada geracao: o efeito reinicia por `generation`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generation, intervalMs, maxIntervalMs]);

  const replace = useCallback((next: ContributionState) => {
    setState(next);
    setConnectionIssue(false);
    setNotFound(false);
    setGeneration((n) => n + 1);
  }, []);

  const refresh = useCallback(() => {
    firstDelayRef.current = 0;
    setGeneration((n) => n + 1);
  }, []);

  return { state, phase: paymentPhase(state), connectionIssue, notFound, replace, refresh };
}
