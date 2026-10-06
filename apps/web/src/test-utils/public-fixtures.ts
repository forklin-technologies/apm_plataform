import type { ContributionState, PublicSchool, Receipt } from "@/lib/api/types";

/** Dados FALSOS no formato do site (camelCase) e da API (snake_case), para os testes do portal. */
export const SCHOOL: PublicSchool = {
  slug: "demo-aurora",
  name: "Escola Aurora (demo)",
  apmName: "APM da Escola Aurora (demo)",
  accentColor: "#0a84ff",
  suggestedAmountsCents: [2000, 3000, 4000],
  allowCustomAmount: true,
  minAmountCents: 1000,
  maxAmountCents: 500000,
  identification: {
    guardianName: "OPTIONAL",
    studentName: "HIDDEN",
    classroom: "HIDDEN",
    contributorEmail: "OPTIONAL",
    contributorPhone: "OPTIONAL",
  },
};

export const SCHOOL_BODY = {
  slug: "demo-aurora",
  name: "Escola Aurora (demo)",
  apm_name: "APM da Escola Aurora (demo)",
  accent_color: "#0a84ff",
  accent_contrast_color: "#ffffff",
  suggested_amounts_cents: [2000, 3000, 4000],
  allow_custom_amount: true,
  min_amount_cents: 1000,
  max_amount_cents: 500000,
  identification: {
    guardian_name: "OPTIONAL",
    student_name: "HIDDEN",
    class_name: "HIDDEN",
    contributor_email: "OPTIONAL",
    contributor_phone: "OPTIONAL",
  },
};

export const TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-AbCde";
export const TXID = "ed57d26c84e449348b47c54223ec68ff";
export const PAYLOAD = `PIX-SANDBOX:${TXID}:2000`;

export function chargeBody(overrides: Record<string, unknown> = {}) {
  return {
    status: "PENDING",
    amount_cents: 2000,
    emv_payload: PAYLOAD,
    expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
    ...overrides,
  };
}

export function stateBody(overrides: Record<string, unknown> = {}, charge: Record<string, unknown> | null = {}) {
  return { status: "PENDING_PAYMENT", amount_cents: 2000, charge: charge === null ? null : chargeBody(charge), ...overrides };
}

export function state(overrides: Partial<ContributionState> = {}): ContributionState {
  return {
    status: "PENDING_PAYMENT",
    amountCents: 2000,
    charge: { status: "PENDING", amountCents: 2000, emvPayload: PAYLOAD, expiresAt: new Date(Date.now() + 10 * 60 * 1000).toISOString() },
    ...overrides,
  };
}

export const RECEIPT: Receipt = {
  status: "PAID",
  referenceCode: "APM-000026",
  amountCents: 2000,
  paidAt: "2026-10-06T13:44:41Z",
  schoolName: "Escola Aurora (demo)",
  method: "PIX",
};

export const RECEIPT_BODY = {
  status: "PAID",
  reference_code: "APM-000026",
  amount_cents: 2000,
  paid_at: "2026-10-06T13:44:41.431609Z",
  school_name: "Escola Aurora (demo)",
  method: "PIX",
};
