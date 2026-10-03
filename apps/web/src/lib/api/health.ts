import { apiGet, type RequestOptions } from "./client";
import type { ReadinessResult, ReadinessStatus } from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readStatus(body: unknown): ReadinessStatus | null {
  if (!isRecord(body)) return null;
  return body.status === "ready" || body.status === "unavailable" ? body.status : null;
}

/**
 * GET /api/health/ready (contrato REAL).
 * - 200 {"status":"ready"}        -> ready
 * - 503 {"status":"unavailable"}  -> unavailable / not-ready (a API respondeu, mas nao esta pronta)
 * - qualquer outra coisa (rede, timeout, 5xx do proxy, corpo estranho) -> unavailable / unreachable
 */
export async function getReadiness(options: RequestOptions = {}): Promise<ReadinessResult> {
  const result = await apiGet<ReadinessStatus>(
    "/api/health/ready",
    (body, status) => {
      const parsed = readStatus(body);
      if (parsed === "ready" && status === 200) return "ready";
      if (parsed === "unavailable" && status === 503) return "unavailable";
      return null;
    },
    options,
  );
  if (result.ok) {
    return result.data === "ready"
      ? { state: "ready" }
      : { state: "unavailable", reason: "not-ready" };
  }
  return { state: "unavailable", reason: "unreachable" };
}
