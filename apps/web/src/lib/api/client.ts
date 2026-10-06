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
  /** Cabecalhos extras (ex.: Idempotency-Key). Nunca sobrescrevem Accept, Content-Type nem X-CSRF-Token. */
  headers?: Record<string, string>;
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
  // A unica parte do `detail` que o site usa: o MES que o servidor nomeia ("The next month to close is
  // 2026-02"). So o formato YYYY-MM sobrevive; o texto do servidor nunca vai para a tela.
  if (body.code === "closing_out_of_sequence" && typeof body.detail === "string") {
    const month = /\b(\d{4}-(?:0[1-9]|1[0-2]))\b/.exec(body.detail);
    if (month) error.detailPeriod = month[1];
  }
  return error;
}

interface Init {
  method: string;
  body?: unknown;
  /** Corpo multipart (upload): o navegador define o Content-Type com o boundary. */
  form?: FormData;
  csrf: boolean;
  /** Resposta binaria (PDF, anexo): em caso de sucesso entrega o Blob; em erro, o problem+json de sempre. */
  blob?: boolean;
}

export interface BlobPayload {
  blob: Blob;
  contentType: string;
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
    const headers: Record<string, string> = { ...options.headers, Accept: init.blob ? "*/*" : "application/json" };
    if (init.body !== undefined) headers["Content-Type"] = "application/json";
    if (init.csrf) {
      const token = options.csrfToken === undefined ? readCsrfToken() : options.csrfToken;
      if (token) headers["X-CSRF-Token"] = token;
    }
    const response = await fetchImpl(path, {
      method: init.method,
      headers,
      body: init.form ?? (init.body === undefined ? undefined : JSON.stringify(init.body)),
      cache: "no-store",
      credentials: "same-origin",
      signal: controller.signal,
    });

    if (init.blob && response.ok) {
      const blob = await response.blob();
      const payload: BlobPayload = { blob, contentType: response.headers.get("Content-Type") ?? blob.type };
      const parsedBlob = parse(payload, response.status);
      return parsedBlob !== null ? { ok: true, data: parsedBlob } : { ok: false, error: { kind: "invalid-response", status: response.status } };
    }

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

const UPLOAD_TIMEOUT_MS = 60_000;

/** POST multipart (upload de anexo). O conteudo do arquivo so vai no corpo: nunca em URL, log ou storage. */
export function apiUpload<T>(
  path: string,
  form: FormData,
  parse: (body: unknown, status: number) => T | null,
  options: SendOptions = {},
): Promise<ApiResult<T>> {
  return request(path, { method: "POST", form, csrf: true }, parse, options, UPLOAD_TIMEOUT_MS);
}

/** GET de um arquivo (PDF, anexo) pela API, com o cookie de sessao. Nunca ha URL publica do arquivo. */
export function apiBlob(path: string, options: RequestOptions = {}): Promise<ApiResult<BlobPayload>> {
  return request(path, { method: "GET", csrf: false, blob: true }, (payload) => payload as BlobPayload, options, UPLOAD_TIMEOUT_MS);
}
