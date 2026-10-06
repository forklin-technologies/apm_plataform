import { apiGet, apiSend, type RequestOptions, type SendOptions } from "./client";
import {
  API_FIELD,
  IDENTIFICATION_FIELDS,
  type FieldRule,
  type IdentificationConfig,
} from "../validation";
import { parseContributionStatus, parsePixStatus } from "../status";
import type {
  ApiResult,
  ContributionState,
  CreateContributionInput,
  CreatedContribution,
  PixCharge,
  PublicSchool,
  Receipt,
} from "./types";

/**
 * Portal publico REAL (docs/public-flow.md). Sem login. Regras:
 * - a escola vem do slug da URL; nenhum id interno circula;
 * - a Idempotency-Key e tao secreta quanto o token: so vai no cabecalho do POST, nunca em URL,
 *   log ou armazenamento (quem gera e guarda so em memoria e o fluxo da pagina);
 * - o cliente NUNCA afirma pagamento: so repassa o status que a API devolveu.
 */

const BASE = "/api/v1/public/schools";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function cents(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

const RULES: readonly FieldRule[] = ["REQUIRED", "OPTIONAL", "HIDDEN"];

/** Campo que a API nao mencionou (ou com valor estranho) fica OCULTO: na duvida, nao pede o dado. */
function parseIdentification(value: unknown): IdentificationConfig {
  const source = isRecord(value) ? value : {};
  const config = {} as IdentificationConfig;
  for (const field of IDENTIFICATION_FIELDS) {
    const raw = source[API_FIELD[field]];
    config[field] = typeof raw === "string" && (RULES as readonly string[]).includes(raw) ? (raw as FieldRule) : "HIDDEN";
  }
  return config;
}

export function parsePublicSchool(body: unknown): PublicSchool | null {
  if (!isRecord(body)) return null;
  const slug = str(body.slug);
  const name = str(body.name);
  const apmName = str(body.apm_name);
  const accentColor = str(body.accent_color);
  const min = cents(body.min_amount_cents);
  const max = cents(body.max_amount_cents);
  if (!slug || !name || !apmName || !accentColor || min === null || max === null) return null;
  if (!/^#[0-9a-fA-F]{6}$/.test(accentColor)) return null;
  if (!Array.isArray(body.suggested_amounts_cents) || typeof body.allow_custom_amount !== "boolean") return null;
  const suggested: number[] = [];
  for (const item of body.suggested_amounts_cents) {
    const value = cents(item);
    if (value === null || value === 0) return null;
    suggested.push(value);
  }
  return {
    slug,
    name,
    apmName,
    accentColor,
    suggestedAmountsCents: suggested,
    allowCustomAmount: body.allow_custom_amount,
    minAmountCents: min,
    maxAmountCents: max,
    identification: parseIdentification(body.identification),
  };
}

function parseCharge(value: unknown): PixCharge | null {
  if (!isRecord(value)) return null;
  const status = parsePixStatus(value.status);
  const amountCents = cents(value.amount_cents);
  const expiresAt = str(value.expires_at);
  const payload = value.emv_payload === null || value.emv_payload === undefined ? null : str(value.emv_payload);
  if (!status || amountCents === null || !expiresAt) return null;
  if (value.emv_payload !== null && value.emv_payload !== undefined && payload === null) return null;
  return { status, amountCents, emvPayload: payload, expiresAt };
}

export function parseContributionState(body: unknown): ContributionState | null {
  if (!isRecord(body)) return null;
  const status = parseContributionStatus(body.status);
  const amountCents = cents(body.amount_cents);
  if (!status || amountCents === null) return null;
  let charge: PixCharge | null = null;
  if (body.charge !== null && body.charge !== undefined) {
    charge = parseCharge(body.charge);
    if (!charge) return null;
  }
  return { status, amountCents, charge };
}

function parseCreated(body: unknown, status: number): CreatedContribution | null {
  if (status !== 201 && status !== 200) return null;
  const state = parseContributionState(body);
  const token = isRecord(body) ? str(body.token) : null;
  return state && token && /^[A-Za-z0-9_-]{22,64}$/.test(token) ? { ...state, token } : null;
}

function parseReceipt(body: unknown, status: number): Receipt | null {
  if (status !== 200 || !isRecord(body) || body.status !== "PAID") return null;
  const referenceCode = str(body.reference_code);
  const amountCents = cents(body.amount_cents);
  const paidAt = str(body.paid_at);
  const schoolName = str(body.school_name);
  const method = str(body.method);
  if (!referenceCode || amountCents === null || !paidAt || !schoolName || !method) return null;
  return { status: "PAID", referenceCode, amountCents, paidAt, schoolName, method };
}

const enc = encodeURIComponent;

/** GET /public/schools/{slug}. */
export function getPublicSchool(slug: string, options?: RequestOptions): Promise<ApiResult<PublicSchool>> {
  return apiGet(`${BASE}/${enc(slug)}`, (body, status) => (status === 200 ? parsePublicSchool(body) : null), options);
}

/** Corpo do POST: so os campos preenchidos, com os nomes da API (a API ainda descarta o que a escola oculta). */
export function contributionBody(input: CreateContributionInput): Record<string, string | number> {
  const body: Record<string, string | number> = { amount_cents: input.amountCents };
  for (const field of IDENTIFICATION_FIELDS) {
    const value = input.identification[field];
    if (value) body[API_FIELD[field]] = value;
  }
  return body;
}

/**
 * POST /public/schools/{slug}/contributions. `idempotencyKey` (uuid) e gerada UMA vez por tentativa
 * pelo chamador: repetir o POST com a mesma chave devolve a mesma contribuicao (nao duplica).
 */
export function createContribution(
  slug: string,
  input: CreateContributionInput,
  idempotencyKey: string,
  options: SendOptions = {},
): Promise<ApiResult<CreatedContribution>> {
  return apiSend("POST", `${BASE}/${enc(slug)}/contributions`, contributionBody(input), parseCreated, {
    ...options,
    headers: { ...options.headers, "Idempotency-Key": idempotencyKey },
  });
}

const parseStateOk = (body: unknown, status: number) => (status === 200 ? parseContributionState(body) : null);

/** GET .../contributions/{token}/charge: o polling da tela do Pix. */
export function getContribution(slug: string, token: string, options?: RequestOptions): Promise<ApiResult<ContributionState>> {
  return apiGet(`${BASE}/${enc(slug)}/contributions/${enc(token)}/charge`, parseStateOk, options);
}

/** POST .../contributions/{token}/charges: novo Pix quando o anterior expirou (a API nao duplica). */
export function renewCharge(slug: string, token: string, options?: SendOptions): Promise<ApiResult<ContributionState>> {
  return apiSend("POST", `${BASE}/${enc(slug)}/contributions/${enc(token)}/charges`, undefined, parseStateOk, options);
}

/** GET .../contributions/{token}/receipt: 200 so pago; 409 enquanto nao; 404 unico para qualquer token ruim. */
export function getReceipt(slug: string, token: string, options?: RequestOptions): Promise<ApiResult<Receipt>> {
  return apiGet(`${BASE}/${enc(slug)}/contributions/${enc(token)}/receipt`, parseReceipt, options);
}

const SANDBOX_PREFIX = "PIX-SANDBOX:";

/** O sandbox de desenvolvimento publica `PIX-SANDBOX:<txid>:<valor>`; so nesse caso existe o botao de simular. */
export function sandboxTxid(payload: string | null): string | null {
  if (!payload?.startsWith(SANDBOX_PREFIX)) return null;
  const txid = payload.slice(SANDBOX_PREFIX.length).split(":")[0] ?? "";
  return /^[A-Za-z0-9_-]{8,128}$/.test(txid) ? txid : null;
}

/** POST /dev/sandbox/pix/{txid}/pay: so desenvolvimento (a API responde 404 fora dele). Nao confirma nada no cliente. */
export function sandboxPay(txid: string, options?: SendOptions): Promise<ApiResult<{ status: string }>> {
  return apiSend(
    "POST",
    `/api/v1/dev/sandbox/pix/${enc(txid)}/pay`,
    {},
    (body) => (isRecord(body) && typeof body.status === "string" ? { status: body.status } : null),
    options,
  );
}
