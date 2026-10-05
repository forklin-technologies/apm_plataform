import { readCsrfToken } from "./csrf";
import type { ApiError, ApiResult, FieldError } from "./types";

/**
 * Cliente HTTP minimo. Regras:
 * - so chama caminhos /api/* na MESMA origem (sem CORS, sem terceiros);
 * - nunca lanca excecao: devolve ApiResult;
 * - nao imprime nada no console (a API fora do ar nao pode inundar o log);
 * - erro da API (application/problem+json) vira ApiError com o `code` estavel; title/detail/
 *   request_id do servidor sao descartados: a interface escreve o proprio texto a partir do `code`;
 * - pedido que muda dados leva X-CSRF-Token (lido do cookie legivel, src/lib/api/csrf.ts).
 */

export interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
}

export interface SendOptions extends RequestOptions {
  /** Sobrescreve o token CSRF (testes). `undefined` = ler do cookie; `null` = nao enviar. */
  csrfToken?: string | null;
}

const DEFAULT_TIMEOUT_MS = 5000;
const SEND_TIMEOUT_MS = 10_000;
const MAX_RETRY_AFTER_S = 3600;
const CODE_SHAPE = /^[a-z][a-z0-9_]{0,63}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseRetryAfter(header: string | null): number | undefined {
  if (header === null || !/^\d{1,6}$/.test(header.trim())) return undefined;
  const seconds = Number(header);
  return seconds >= 1 ? Math.min(seconds, MAX_RETRY_AFTER_S) : undefined;
}

function parseFieldErrors(value: unknown): FieldError[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const out: FieldError[] = [];
  for (const item of value.slice(0, 20)) {
    if (isRecord(item) && typeof item.field === "string" && typeof item.code === "string") {
      if (CODE_SHAPE.test(item.code) && item.field.length <= 64) out.push({ field: item.field, code: item.code });
    }
  }
  return out.length > 0 ? out : undefined;
}

/** Converte um problem+json em ApiError. Sem `code` valido, e um erro HTTP comum. */
function toApiError(response: Response, body: unknown): ApiError {
  if (!isRecord(body) || typeof body.code !== "string" || !CODE_SHAPE.test(body.code)) {
    return response.ok
      ? { kind: "invalid-response", status: response.status }
      : { kind: "http", status: response.status };
  }
  const error: ApiError = { kind: "problem", status: response.status, code: body.code };
  const retry = parseRetryAfter(response.headers.get("Retry-After"));
  if (retry !== undefined) error.retryAfterSeconds = retry;
  const fields = parseFieldErrors(body.errors);
  if (fields) error.fields = fields;
  return error;
}

interface Init {
  method: string;
  body?: unknown;
  csrf: boolean;
}

async function request<T>(
  path: string,
  init: Init,
  parse: (body: unknown, status: number) => T | null,
  options: SendOptions,
  defaultTimeoutMs: number,
): Promise<ApiResult<T>> {
  if (!path.startsWith("/api/")) {
    return { ok: false, error: { kind: "invalid-response" } };
  }
  const { signal, timeoutMs = defaultTimeoutMs, fetchImpl = fetch } = options;

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
    const headers: Record<string, string> = { Accept: "application/json" };
    if (init.body !== undefined) headers["Content-Type"] = "application/json";
    if (init.csrf) {
      const token = options.csrfToken === undefined ? readCsrfToken() : options.csrfToken;
      if (token) headers["X-CSRF-Token"] = token;
    }
    const response = await fetchImpl(path, {
      method: init.method,
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      cache: "no-store",
      credentials: "same-origin",
      signal: controller.signal,
    });

    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null; // 204, 500 do proxy sem corpo, HTML, etc.
    }

    const parsed = parse(body, response.status);
    if (parsed !== null) return { ok: true, data: parsed };
    return { ok: false, error: toApiError(response, body) };
  } catch {
    return { ok: false, error: { kind: timedOut ? "timeout" : "network" } };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}

export function apiGet<T>(
  path: string,
  parse: (body: unknown, status: number) => T | null,
  options: RequestOptions = {},
): Promise<ApiResult<T>> {
  return request(path, { method: "GET", csrf: false }, parse, options, DEFAULT_TIMEOUT_MS);
}

/** POST/PUT/PATCH/DELETE com corpo JSON opcional. Nao repete sozinho: pedido que muda dados nao e idempotente. */
export function apiSend<T>(
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  body: unknown,
  parse: (body: unknown, status: number) => T | null,
  options: SendOptions = {},
): Promise<ApiResult<T>> {
  return request(path, { method, body, csrf: true }, parse, options, SEND_TIMEOUT_MS);
}
