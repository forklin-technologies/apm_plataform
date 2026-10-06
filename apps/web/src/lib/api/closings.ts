import { apiBlob, apiGet, apiSend, type BlobPayload, type RequestOptions, type SendOptions } from "./client";
import type { ApiResult, Closing, ClosingVerification, Page } from "./types";

/**
 * Fechamento mensal REAL (docs/statement.md). Os numeros sao colunas do fechamento (a API nao soma e o
 * site tambem nao). Fechar so INSERE; quem calcula tudo e o banco. Os meses fecham em ordem.
 */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
const int = (v: unknown): number | null => (typeof v === "number" && Number.isSafeInteger(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v.length > 0 ? v : null);
const optStr = (v: unknown): string | null | undefined => (v === null || v === undefined ? null : typeof v === "string" ? v : undefined);
const optInt = (v: unknown): number | null | undefined => (v === null || v === undefined ? null : int(v) === null ? undefined : (v as number));

export function parseClosing(v: unknown): Closing | null {
  if (!isRecord(v)) return null;
  const id = str(v.id);
  const schoolId = str(v.school_id);
  const period = str(v.period);
  const periodStart = str(v.period_start);
  const periodEnd = str(v.period_end);
  const timezone = str(v.timezone);
  const n = {
    openingBalanceCents: int(v.opening_balance_cents),
    contributionsInCents: int(v.contributions_in_cents),
    otherInCents: int(v.other_in_cents),
    refundsInCents: int(v.refunds_in_cents),
    totalInCents: int(v.total_in_cents),
    expensesOutCents: int(v.expenses_out_cents),
    reimbursementsOutCents: int(v.reimbursements_out_cents),
    totalOutCents: int(v.total_out_cents),
    closingBalanceCents: int(v.closing_balance_cents),
    pendingReimbursementsCents: int(v.pending_reimbursements_cents),
    closingAfterPendingCents: int(v.closing_after_pending_cents),
    entriesCount: int(v.entries_count),
  };
  const entriesHash = str(v.entries_hash);
  const closedAt = str(v.closed_at);
  const bank = optInt(v.bank_balance_reported_cents);
  const diff = optInt(v.bank_difference_cents);
  const reportRef = optStr(v.report_ref);
  const reopenedAt = optStr(v.reopened_at);
  const reopenReason = optStr(v.reopen_reason);
  if (!id || !schoolId || !period || !/^\d{4}-(0[1-9]|1[0-2])$/.test(period) || !periodStart || !periodEnd || !timezone || !entriesHash || !closedAt) return null;
  if (Object.values(n).some((x) => x === null) || [bank, diff, reportRef, reopenedAt, reopenReason].some((x) => x === undefined)) return null;
  return {
    id, schoolId, period, periodStart, periodEnd, timezone, ...(n as Record<keyof typeof n, number>), entriesHash, closedAt,
    bankBalanceReportedCents: bank as number | null, bankDifferenceCents: diff as number | null, reportRef: reportRef as string | null,
    reopenedAt: reopenedAt as string | null, reopenReason: reopenReason as string | null,
  };
}

const enc = encodeURIComponent;
const base = (schoolId: string) => `/api/v1/schools/${enc(schoolId)}/closings`;
const closingOk = (body: unknown, status: number) => (status === 200 || status === 201 ? parseClosing(body) : null);

/** GET .../closings: os fechamentos (com os reabertos). Aberto ou reaberto, a tela decide por `reopenedAt`. */
export function listClosings(
  schoolId: string,
  params: { cursor?: string; limit?: number } = {},
  options?: RequestOptions,
): Promise<ApiResult<Page<Closing>>> {
  const q = [`include_reopened=true`, params.cursor ? `cursor=${enc(params.cursor)}` : "", params.limit ? `limit=${params.limit}` : ""].filter(Boolean).join("&");
  return apiGet(
    `${base(schoolId)}?${q}`,
    (body, status) => {
      if (status !== 200 || !isRecord(body) || !Array.isArray(body.items)) return null;
      const items: Closing[] = [];
      for (const raw of body.items) {
        const c = parseClosing(raw);
        if (!c) return null;
        items.push(c);
      }
      const next = body.next_cursor;
      if (next !== null && next !== undefined && (typeof next !== "string" || next.length === 0 || next.length > 500)) return null;
      return { items, nextCursor: typeof next === "string" ? next : null };
    },
    options,
  );
}

/** POST .../closings: `{period, bank_balance_reported_cents?}`. So o INSERT: o banco calcula tudo. */
export function createClosing(
  schoolId: string,
  input: { period: string; bankBalanceReportedCents?: number },
  options?: SendOptions,
): Promise<ApiResult<Closing>> {
  const body: Record<string, string | number> = { period: input.period };
  if (input.bankBalanceReportedCents !== undefined) body.bank_balance_reported_cents = input.bankBalanceReportedCents;
  return apiSend("POST", base(schoolId), body, closingOk, options);
}

/** POST .../closings/{id}/verify: confere o fechamento com o razao (`verified`). */
export function verifyClosing(schoolId: string, closingId: string, options?: SendOptions): Promise<ApiResult<ClosingVerification>> {
  return apiSend(
    "POST",
    `${base(schoolId)}/${enc(closingId)}/verify`,
    undefined,
    (body, status) => {
      if (status !== 200 || !isRecord(body)) return null;
      const id = str(body.closing_id);
      const hash = str(body.entries_hash);
      return id && hash && typeof body.verified === "boolean" ? { closingId: id, verified: body.verified, entriesHash: hash } : null;
    },
    options,
  );
}

/** POST .../closings/{id}/reopen: so `months:reopen`; `reason` com 10 a 500 caracteres (a API revalida). */
export function reopenClosing(schoolId: string, closingId: string, reason: string, options?: SendOptions): Promise<ApiResult<Closing>> {
  return apiSend("POST", `${base(schoolId)}/${enc(closingId)}/reopen`, { reason }, closingOk, options);
}

/** GET .../closings/{id}/report.pdf pela API, com a sessao. Reaberto: 409 `closing_reopened`. */
export function downloadClosingPdf(schoolId: string, closingId: string, options?: RequestOptions): Promise<ApiResult<BlobPayload>> {
  return apiBlob(`${base(schoolId)}/${enc(closingId)}/report.pdf`, options);
}
