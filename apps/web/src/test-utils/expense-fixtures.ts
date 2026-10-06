import type { ExpenseDetail, ExpenseSummary } from "@/lib/api/types";

/** Dados FALSOS para os testes de despesas e fechamento. snake_case = formato da API. */
export const AUTHOR_ID = "7ff63d82-d8f0-40b3-9f27-778469d4c711";
export const MANAGER_ID = "e25e2070-9cd7-4294-9eb6-8e0a0a00df00";
export const CATEGORY = { id: "c62607ae-749d-4640-b4cf-72f8ee5661e2", key: "school_supplies", name: "Compra de material" };

export function summaryBody(overrides: Record<string, unknown> = {}) {
  return {
    id: "405ce5fe-8bdb-4899-95a8-3a4295b28d16",
    reference_code: 24,
    status: "SUBMITTED",
    amount_cents: 20000,
    approved_amount_cents: null,
    category: CATEGORY,
    occurred_at: "2026-10-05T15:00:00Z",
    description: "Material de pintura",
    vendor: "Papelaria Exemplo",
    paid_by: "COLLABORATOR",
    submitted_by_user_id: AUTHOR_ID,
    attachments_count: 1,
    created_at: "2026-10-06T13:41:42Z",
    updated_at: "2026-10-06T13:41:42Z",
    ...overrides,
  };
}

export function attachmentBody(overrides: Record<string, unknown> = {}) {
  return {
    id: "a1111111-1111-4111-8111-111111111111",
    kind: "INVOICE",
    file_name: "nota.pdf",
    content_type: "application/pdf",
    size_bytes: 20480,
    sha256: "f".repeat(64),
    uploaded_by_user_id: AUTHOR_ID,
    created_at: "2026-10-06T13:45:00Z",
    ...overrides,
  };
}

export function detailBody(overrides: Record<string, unknown> = {}) {
  return {
    ...summaryBody(),
    purchase_reason: "Aula de artes",
    payment_method: "PIX",
    approved_by_user_id: null,
    approved_at: null,
    decision_reason: null,
    correction_reason: null,
    settled_at: null,
    attachments: [attachmentBody()],
    reimbursement: null,
    ...overrides,
  };
}

export function summary(overrides: Partial<ExpenseSummary> = {}): ExpenseSummary {
  return {
    id: "405ce5fe-8bdb-4899-95a8-3a4295b28d16",
    referenceCode: 24,
    status: "SUBMITTED",
    amountCents: 20000,
    approvedAmountCents: null,
    category: CATEGORY,
    occurredAt: "2026-10-05T15:00:00Z",
    description: "Material de pintura",
    vendor: "Papelaria Exemplo",
    paidBy: "COLLABORATOR",
    submittedByUserId: AUTHOR_ID,
    attachmentsCount: 1,
    createdAt: "2026-10-06T13:41:42Z",
    updatedAt: "2026-10-06T13:41:42Z",
    ...overrides,
  };
}

export function detail(overrides: Partial<ExpenseDetail> = {}): ExpenseDetail {
  return {
    ...summary(),
    purchaseReason: "Aula de artes",
    paymentMethod: "PIX",
    approvedByUserId: null,
    approvedAt: null,
    decisionReason: null,
    correctionReason: null,
    settledAt: null,
    attachments: [
      { id: "a1111111-1111-4111-8111-111111111111", kind: "INVOICE", fileName: "nota.pdf", contentType: "application/pdf", sizeBytes: 20480, uploadedByUserId: AUTHOR_ID, createdAt: "2026-10-06T13:45:00Z" },
    ],
    reimbursement: null,
    ...overrides,
  };
}

export function closingBody(overrides: Record<string, unknown> = {}) {
  return {
    id: "9a560fdf-0a78-482f-8c74-9965db69cdad",
    school_id: "17410521-ded4-4213-a249-1beaa910ebd7",
    period: "2026-01",
    period_start: "2026-01-01",
    period_end: "2026-01-31",
    timezone: "America/Sao_Paulo",
    opening_balance_cents: 10000,
    contributions_in_cents: 45000,
    other_in_cents: 0,
    refunds_in_cents: 0,
    total_in_cents: 45000,
    expenses_out_cents: 3000,
    reimbursements_out_cents: 17500,
    total_out_cents: 20500,
    closing_balance_cents: 34500,
    pending_reimbursements_cents: 4500,
    closing_after_pending_cents: 30000,
    entries_count: 7,
    entries_hash: "98da742317c7d6939ec9c29ff332114343895e803b9664dc42e8e6e109683307",
    bank_balance_reported_cents: 35000,
    bank_difference_cents: 500,
    closed_by_user_id: MANAGER_ID,
    closed_at: "2026-10-06T15:00:19Z",
    report_ref: null,
    reopened_at: null,
    reopened_by_user_id: null,
    reopen_reason: null,
    breakdown: {},
    ...overrides,
  };
}
