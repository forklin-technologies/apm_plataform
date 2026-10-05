"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getReadiness } from "@/lib/api/health";
import type { ReadinessResult } from "@/lib/api/types";

export type ApiStatus =
  | { state: "checking" }
  | { state: "ready" }
  | { state: "unavailable"; reason: "not-ready" | "unreachable" };

const HIDDEN_MIN_MS = 60_000;

export interface UseApiStatusOptions {
  check?: () => Promise<ReadinessResult>;
  /** Intervalo quando a API esta no ar. */
  readyIntervalMs?: number;
  /** Primeiro intervalo quando esta fora; dobra a cada falha ate downMaxMs. */
  downIntervalMs?: number;
  downMaxMs?: number;
}

/**
 * Consulta GET /api/health/ready em intervalo moderado.
 * - com a aba oculta consulta no maximo a cada 60 s e, ao voltar a ficar visivel, consulta na hora;
 * - com a API fora, espaca as consultas (backoff) para nao inundar rede nem console;
 * - nunca lanca: qualquer falha vira "unavailable".
 */
export function useApiStatus(options: UseApiStatusOptions = {}): ApiStatus & { refresh: () => void } {
  const {
    check = getReadiness,
    readyIntervalMs = 30_000,
    downIntervalMs = 15_000,
    downMaxMs = 60_000,
  } = options;
  const [status, setStatus] = useState<ApiStatus>({ state: "checking" });
  const checkRef = useRef(check);
  const runRef = useRef<() => void>(() => {});

  useEffect(() => {
    checkRef.current = check;
  }, [check]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    let inFlight = false;

    // Documento oculto (aba em segundo plano, portal do Maestri): consulta mais devagar, nunca para.
    // Parar deixaria o chip congelado num estado velho para quem ainda esta olhando a tela.
    const schedule = (ms: number) => {
      clearTimeout(timer);
      if (cancelled) return;
      timer = setTimeout(run, document.visibilityState === "hidden" ? Math.max(ms, HIDDEN_MIN_MS) : ms);
    };

    async function run() {
      if (cancelled || inFlight) return;
      inFlight = true;
      clearTimeout(timer);
      let result: ReadinessResult;
      try {
        result = await checkRef.current();
      } catch {
        result = { state: "unavailable", reason: "unreachable" };
      }
      inFlight = false;
      if (cancelled) return;
      if (result.state === "ready") {
        failures = 0;
        setStatus({ state: "ready" });
        schedule(readyIntervalMs);
      } else {
        failures += 1;
        setStatus({ state: "unavailable", reason: result.reason });
        schedule(Math.min(downIntervalMs * 2 ** (failures - 1), downMaxMs));
      }
    }

    runRef.current = () => {
      void run();
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") void run();
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("online", onVisibility);
    void run();

    return () => {
      cancelled = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("online", onVisibility);
    };
  }, [readyIntervalMs, downIntervalMs, downMaxMs]);

  const refresh = useCallback(() => runRef.current(), []);
  return { ...status, refresh };
}
