import type { Page, PendingEntry, StatementEntry, StatementSummary } from "@/lib/api/types";

/** Dados FALSOS no formato da API (snake_case) e do site (camelCase), para os testes do painel. */
export const SCHOOL_ID = "22222222-2222-4222-8222-222222222222";

export const SUMMARY_BODY = {
  period: "2026-10",
  period_start: "2026-10-01",
  period_end: "2026-10-31",
  timezone: "America/Sao_Paulo",
  opening_balance_cents: 39710,
  contributions_in_cents: 45000,
  other_in_cents: 1000,
  refunds_in_cents: 500,
  total_in_cents: 46500,
  expenses_out_cents: 300,
  reimbursements_out_cents: 17500,
  total_out_cents: 17800,
  closing_balance_cents: 68410,
  pending_reimbursements_cents: 4500,
  balance_after_pending_cents: 63910,
  entries_count: 14,
};

export const SUMMARY: StatementSummary = {
  period: "2026-10",
  periodStart: "2026-10-01",
  periodEnd: "2026-10-31",
  timezone: "America/Sao_Paulo",
  openingBalanceCents: 39710,
  contributionsInCents: 45000,
  otherInCents: 1000,
  refundsInCents: 500,
  totalInCents: 46500,
  expensesOutCents: 300,
  reimbursementsOutCents: 17500,
  totalOutCents: 17800,
  closingBalanceCents: 68410,
  pendingReimbursementsCents: 4500,
  balanceAfterPendingCents: 63910,
  entriesCount: 14,
};

export function entryBody(overrides: Record<string, unknown> = {}) {
  return {
    transaction_id: "93c097d5-6169-4204-96ad-80a398e9dcc9",
    reference_code: 21,
    kind: "CONTRIBUTION",
    display_type: "INCOME",
    direction: "IN",
    settled_at: "2026-10-06T13:41:42Z",
    occurred_at: "2026-10-06T13:41:42Z",
    local_date: "2026-10-06",
    amount_cents: 3000,
    signed_amount_cents: 3000,
    late_adjustment: false,
    status: "PAID",
    status_label: "PAID",
    origin_type: "GUARDIAN",
    origin_user_id: null,
    origin_label: "Maria Teste",
    category_id: "0b2a99cf-59dc-42de-8635-b6076431acd9",
    category_key: "parent_contribution",
    report_group: "CONTRIBUTIONS",
    description: "Contribuição de pais",
    beneficiary_user_id: null,
    beneficiary_label: null,
    opening_balance_cents: 39710,
    running_balance_cents: 42710,
    ...overrides,
  };
}

export function entry(overrides: Partial<StatementEntry> = {}): StatementEntry {
  return {
    transactionId: "93c097d5-6169-4204-96ad-80a398e9dcc9",
    referenceCode: 21,
    kind: "CONTRIBUTION",
    displayType: "INCOME",
    direction: "IN",
    occurredAt: "2026-10-06T13:41:42Z",
    localDate: "2026-10-06",
    amountCents: 3000,
    signedAmountCents: 3000,
    lateAdjustment: false,
    status: "PAID",
    statusLabel: "PAID",
    originLabel: "Maria Teste",
    beneficiaryLabel: null,
    categoryKey: "parent_contribution",
    description: "Contribuição de pais",
    runningBalanceCents: 42710,
    ...overrides,
  };
}

export function pendingBody(overrides: Record<string, unknown> = {}) {
  return {
    transaction_id: "ada329a6-534a-4cf3-97f3-395e49de902a",
    reference_code: 16,
    kind: "REIMBURSEMENT",
    direction: "OUT",
    status: "PENDING",
    amount_cents: 4500,
    section: "PAYABLE",
    occurred_at: "2026-09-25T17:13:12Z",
    ...overrides,
  };
}

export function pending(overrides: Partial<PendingEntry> = {}): PendingEntry {
  return {
    transactionId: "ada329a6-534a-4cf3-97f3-395e49de902a",
    referenceCode: 16,
    kind: "REIMBURSEMENT",
    direction: "OUT",
    status: "PENDING",
    amountCents: 4500,
    section: "PAYABLE",
    occurredAt: "2026-09-25T17:13:12Z",
    ...overrides,
  };
}

export function page<T>(items: T[], nextCursor: string | null = null): Page<T> {
  return { items, nextCursor };
}
