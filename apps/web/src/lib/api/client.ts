import type { ApiError, ApiResult } from "./types";

/**
 * Cliente HTTP minimo. Regras:
 * - so chama caminhos /api/* na MESMA origem (sem CORS, sem terceiros);
 * - nunca lanca excecao: devolve ApiResult;
 * - nao imprime nada no console (a API fora do ar nao pode inundar o log).
 */

export interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
}

const DEFAULT_TIMEOUT_MS = 5000;

export async function apiGet<T>(
  path: string,
  parse: (body: unknown, status: number) => T | null,
  options: RequestOptions = {},
): Promise<ApiResult<T>> {
  if (!path.startsWith("/api/")) {
    return { ok: false, error: { kind: "invalid-response" } };
  }
  const { signal, timeoutMs = DEFAULT_TIMEOUT_MS, fetchImpl = fetch } = options;

  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const onAbort = () => controller.abort();
  signal?.addEventListener("abort", onAbort, { once: true });
  if (signal?.aborted) controller.abort();

  try {
    const response = await fetchImpl(path, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
      credentials: "same-origin",
      signal: controller.signal,
    });

    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null; // 500 do proxy sem corpo, HTML, etc.
    }

    const parsed = parse(body, response.status);
    if (parsed !== null) return { ok: true, data: parsed };

    const error: ApiError = response.ok
      ? { kind: "invalid-response", status: response.status }
      : { kind: "http", status: response.status };
    return { ok: false, error };
  } catch {
    return { ok: false, error: { kind: timedOut ? "timeout" : "network" } };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}
