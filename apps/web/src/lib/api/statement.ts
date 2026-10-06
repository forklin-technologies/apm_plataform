import { apiGet, apiSend, type RequestOptions, type SendOptions } from "./client";
import { API_FIELD, IDENTIFICATION_FIELDS } from "../validation";
import type {
  ApiResult,
  CashContributionInput,
  CashContributionResult,
  Page,
  PendingEntry,
  StatementEntry,
  StatementSummary,
} from "./types";
import type { MovementKind } from "../status";

/**
 * Painel REAL (docs/statement.md). A escola do caminho e o `school.id` do vinculo ATIVO da sessao
 * (GET /auth/me): o cliente nunca escolhe tenant, e a API ainda confere o id contra a sessao (outra
 * escola = o mesmo 404 de uma que nao existe). Dinheiro em centavos; o site nao soma nem recalcula nada.
 */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

const int = (value: unknown): number | null => (typeof value === "number" && Number.isSafeInteger(value) ? value : null);
const str = (value: unknown): string | null => (typeof value === "string" && value.length > 0 ? value : null);
const optStr = (value: unknown): string | null | undefined =>
  value === null || value === undefined ? null : typeof value === "string" ? value : undefined;

const KINDS: readonly MovementKind[] = ["CONTRIBUTION", "EXPENSE", "REIMBURSEMENT", "REFUND"];
const parseKind = (value: unknown): MovementKind | null =>
  typeof value === "string" && (KINDS as readonly string[]).includes(value) ? (value as MovementKind) : null;
const parseDirection = (value: unknown): "IN" | "OUT" | null => (value === "IN" || value === "OUT" ? value : null);

export const PERIOD_PATTERN = /^\d{4}-(0[1-9]|1[0-2])$/;

export function parseSummary(body: unknown): StatementSummary | null {
  if (!isRecord(body)) return null;
  const period = str(body.period);
  const periodStart = str(body.period_start);
  const periodEnd = str(body.period_end);
  const timezone = str(body.timezone);
  const numbers = {
    openingBalanceCents: int(body.opening_balance_cents),
    contributionsInCents: int(body.contributions_in_cents),
    otherInCents: int(body.other_in_cents),
    refundsInCents: int(body.refunds_in_cents),
    totalInCents: int(body.total_in_cents),
    expensesOutCents: int(body.expenses_out_cents),
    reimbursementsOutCents: int(body.reimbursements_out_cents),
    totalOutCents: int(body.total_out_cents),
    closingBalanceCents: int(body.closing_balance_cents),
    pendingReimbursementsCents: int(body.pending_reimbursements_cents),
    balanceAfterPendingCents: int(body.balance_after_pending_cents),
    entriesCount: int(body.entries_count),
  };
  if (!period || !PERIOD_PATTERN.test(period) || !periodStart || !periodEnd || !timezone) return null;
  if (Object.values(numbers).some((v) => v === null)) return null;
  return { period, periodStart, periodEnd, timezone, ...(numbers as Record<keyof typeof numbers, number>) };
}

function parseEntry(value: unknown): StatementEntry | null {
  if (!isRecord(value)) return null;
  const transactionId = str(value.transaction_id);
  const referenceCode = int(value.reference_code);
  const kind = parseKind(value.kind);
  const direction = parseDirection(value.direction);
  const displayType = value.display_type;
  const occurredAt = str(value.occurred_at);
  const localDate = str(value.local_date);
  const amountCents = int(value.amount_cents);
  const signedAmountCents = int(value.signed_amount_cents);
  const status = str(value.status);
  const statusLabel = str(value.status_label) ?? status;
  const categoryKey = str(value.category_key);
  const running = int(value.running_balance_cents);
  const originLabel = optStr(value.origin_label);
  const beneficiaryLabel = optStr(value.beneficiary_label);
  const description = optStr(value.description);
  if (
    !transactionId || referenceCode === null || !kind || !direction || !occurredAt || !localDate ||
    amountCents === null || signedAmountCents === null || !status || !categoryKey || running === null ||
    originLabel === undefined || beneficiaryLabel === undefined || description === undefined ||
    (displayType !== "INCOME" && displayType !== "EXPENSE" && displayType !== "REFUND")
  ) {
    return null;
  }
  return {
    transactionId, referenceCode, kind, displayType, direction, occurredAt, localDate, amountCents, signedAmountCents,
    lateAdjustment: value.late_adjustment === true, status, statusLabel: statusLabel ?? status, originLabel, beneficiaryLabel, categoryKey, description,
    runningBalanceCents: running,
  };
}

function parsePending(value: unknown): PendingEntry | null {
  if (!isRecord(value)) return null;
  const transactionId = str(value.transaction_id);
  const referenceCode = int(value.reference_code);
  const kind = parseKind(value.kind);
  const direction = parseDirection(value.direction);
  const status = str(value.status);
  const amountCents = int(value.amount_cents);
  const section = str(value.section);
  const occurredAt = str(value.occurred_at);
  if (!transactionId || referenceCode === null || !kind || !direction || !status || amountCents === null || !section || !occurredAt) return null;
  return { transactionId, referenceCode, kind, direction, status, amountCents, section, occurredAt };
}

function parsePage<T>(body: unknown, item: (value: unknown) => T | null): Page<T> | null {
  if (!isRecord(body) || !Array.isArray(body.items)) return null;
  const items: T[] = [];
  for (const raw of body.items) {
    const parsed = item(raw);
    if (!parsed) return null;
    items.push(parsed);
  }
  const next = body.next_cursor;
  if (next !== null && next !== undefined && (typeof next !== "string" || next.length === 0 || next.length > 500)) return null;
  return { items, nextCursor: typeof next === "string" ? next : null };
}

const enc = encodeURIComponent;
const school = (schoolId: string) => `/api/v1/schools/${enc(schoolId)}`;

function query(params: Record<string, string | number | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== "")
    .map(([key, value]) => `${key}=${enc(String(value))}`);
  return parts.length > 0 ? `?${parts.join("&")}` : "";
}

/** GET .../statement/summary. Sem `period`, a API usa o mes corrente no fuso da escola e o devolve. */
export function getSummary(schoolId: string, period?: string, options?: RequestOptions): Promise<ApiResult<StatementSummary>> {
  return apiGet(`${school(schoolId)}/statement/summary${query({ period })}`, (body, status) => (status === 200 ? parseSummary(body) : null), options);
}

export interface StatementQuery {
  period?: string;
  kind?: MovementKind;
  cursor?: string;
  limit?: number;
}

/** GET .../statement: a pagina de lancamentos do mes (cursor opaco). */
export function getStatement(schoolId: string, params: StatementQuery = {}, options?: RequestOptions): Promise<ApiResult<Page<StatementEntry>>> {
  return apiGet(
    `${school(schoolId)}/statement${query({ period: params.period, kind: params.kind, cursor: params.cursor, limit: params.limit })}`,
    (body, status) => (status === 200 ? parsePage(body, parseEntry) : null),
    options,
  );
}

/** GET .../statement/pending: o que esta fora do saldo. */
export function getPending(
  schoolId: string,
  params: { cursor?: string; limit?: number } = {},
  options?: RequestOptions,
): Promise<ApiResult<Page<PendingEntry>>> {
  return apiGet(
    `${school(schoolId)}/statement/pending${query({ cursor: params.cursor, limit: params.limit })}`,
    (body, status) => (status === 200 ? parsePage(body, parsePending) : null),
    options,
  );
}

/** Link do PDF das contribuicoes do mes (GET .../reports/contributions.pdf). E uma navegacao, nao um fetch. */
export function contributionsPdfHref(schoolId: string, period: string): string {
  return `${school(schoolId)}/reports/contributions.pdf${query({ period })}`;
}

function parseCash(body: unknown, status: number): CashContributionResult | null {
  if ((status !== 201 && status !== 200) || !isRecord(body)) return null;
  const id = str(body.id);
  const referenceCode = str(body.reference_code);
  const state = str(body.status);
  const amountCents = int(body.amount_cents);
  const method = str(body.method);
  return id && referenceCode && state && amountCents !== null && method ? { id, referenceCode, status: state, amountCents, method } : null;
}

/**
 * POST .../contributions (tesouraria: dinheiro, transferencia ou outra forma; `contributions:record_cash`).
 * `idempotencyKey` (uuid) e gerada UMA vez por tentativa pelo chamador: repetir devolve a mesma contribuicao (200).
 */
export function recordCashContribution(
  schoolId: string,
  input: CashContributionInput,
  idempotencyKey: string,
  options: SendOptions = {},
): Promise<ApiResult<CashContributionResult>> {
  const body: Record<string, string | number> = {
    amount_cents: input.amountCents,
    method: input.method,
    category_key: input.categoryKey,
  };
  for (const field of IDENTIFICATION_FIELDS) {
    const value = input.identification[field];
    if (value) body[API_FIELD[field]] = value;
  }
  return apiSend("POST", `${school(schoolId)}/contributions`, body, parseCash, {
    ...options,
    headers: { ...options.headers, "Idempotency-Key": idempotencyKey },
  });
}
