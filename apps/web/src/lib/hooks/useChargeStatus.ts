"use client";

import { useEffect, useRef, useState } from "react";
import type { ApiResult, PixCharge } from "@/lib/api/types";
import { parsePixStatus, pixStatusView, type PixChargeStatus } from "@/lib/status";

export interface UseChargeStatusOptions {
  initial: PixCharge;
  /** Consulta o status na camada de dados. O hook NUNCA calcula status: so repassa o que chega. */
  fetchCharge: () => Promise<ApiResult<PixCharge>>;
  intervalMs?: number;
  maxIntervalMs?: number;
}

export interface ChargeStatusState {
  charge: PixCharge;
  /** Status validado; null se a camada de dados mandou algo desconhecido. */
  status: PixChargeStatus | null;
  /** True enquanto a ultima consulta falhou (rede) e o hook continua tentando. */
  connectionIssue: boolean;
  /** True se a cobranca nao existe mais (404). */
  notFound: boolean;
}

/**
 * Polling do status da cobranca Pix. A unica fonte de status e `fetchCharge`.
 * Para quando o status e final (pago, expirado, cancelado) ou a cobranca some.
 */
export function useChargeStatus({
  initial,
  fetchCharge,
  intervalMs = 2000,
  maxIntervalMs = 10_000,
}: UseChargeStatusOptions): ChargeStatusState {
  const [charge, setCharge] = useState<PixCharge>(initial);
  const [connectionIssue, setConnectionIssue] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const fetchRef = useRef(fetchCharge);

  useEffect(() => {
    fetchRef.current = fetchCharge;
  }, [fetchCharge]);

  const token = initial.token;
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;

    async function poll() {
      const result = await fetchRef.current();
      if (cancelled) return;
      if (result.ok) {
        failures = 0;
        setConnectionIssue(false);
        setCharge(result.data);
        if (pixStatusView(parsePixStatus(result.data.status)).final) return;
      } else if (result.error.kind === "not-found") {
        setNotFound(true);
        return;
      } else {
        failures += 1;
        setConnectionIssue(true);
      }
      const wait = Math.min(intervalMs * 2 ** Math.max(0, failures - 1), maxIntervalMs);
      timer = setTimeout(poll, wait);
    }

    timer = setTimeout(poll, intervalMs);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [token, intervalMs, maxIntervalMs]);

  return { charge, status: parsePixStatus(charge.status), connectionIssue, notFound };
}
