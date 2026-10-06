import { apiBlob, apiGet, apiSend, apiUpload, type BlobPayload, type RequestOptions, type SendOptions } from "./client";
import type {
  ApiResult,
  Attachment,
  AttachmentKind,
  ExpenseCategory,
  ExpenseDetail,
  ExpenseInput,
  ExpenseStatus,
  ExpenseSummary,
  ExpenseUpdate,
  Page,
  PaidBy,
  PaymentMethod,
  Reimbursement,
} from "./types";

/**
 * Despesas REAIS (docs/expenses.md). A escola do caminho e o `school.id` do vinculo ativo; a API ainda
 * confere contra a sessao. O site nunca decide estado: mostra o que a API devolve. A API devolve so o id
 * de quem enviou (nao o nome). O conteudo do anexo so vai no corpo multipart, e o download passa pela API
 * com o cookie de sessao (nunca ha URL publica).
 */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

const int = (v: unknown): number | null => (typeof v === "number" && Number.isSafeInteger(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v.length > 0 ? v : null);
const optStr = (v: unknown): string | null | undefined => (v === null || v === undefined ? null : typeof v === "string" ? v : undefined);
const optInt = (v: unknown): number | null | undefined => (v === null || v === undefined ? null : int(v) === null ? undefined : (v as number));

export const EXPENSE_STATUSES: readonly ExpenseStatus[] = ["DRAFT", "SUBMITTED", "CORRECTION_REQUESTED", "APPROVED", "REJECTED", "PAID", "CANCELLED"];
const PAID_BY: readonly PaidBy[] = ["APM", "COLLABORATOR"];
const METHODS: readonly PaymentMethod[] = ["PIX", "CARD", "CASH", "OTHER"];
const KINDS: readonly AttachmentKind[] = ["INVOICE", "PAYMENT_PROOF", "OTHER"];
const oneOf = <T extends string>(list: readonly T[], v: unknown): T | null => (typeof v === "string" && (list as readonly string[]).includes(v) ? (v as T) : null);

function parseCategory(v: unknown): ExpenseCategory | null {
  if (!isRecord(v)) return null;
  const id = str(v.id);
  const key = str(v.key);
  const name = str(v.name);
  return id && key && name ? { id, key, name } : null;
}

function parseSummary(v: unknown): ExpenseSummary | null {
  if (!isRecord(v)) return null;
  const id = str(v.id);
  const referenceCode = int(v.reference_code);
  const status = oneOf(EXPENSE_STATUSES, v.status);
  const amountCents = int(v.amount_cents);
  const approved = optInt(v.approved_amount_cents);
  const category = parseCategory(v.category);
  const occurredAt = str(v.occurred_at);
  const description = typeof v.description === "string" ? v.description : null;
  const vendor = optStr(v.vendor);
  const paidBy = oneOf(PAID_BY, v.paid_by);
  const submittedByUserId = str(v.submitted_by_user_id);
  const attachmentsCount = int(v.attachments_count);
  const createdAt = str(v.created_at);
  const updatedAt = str(v.updated_at);
  if (
    !id || referenceCode === null || !status || amountCents === null || approved === undefined || !category || !occurredAt ||
    description === null || vendor === undefined || !paidBy || !submittedByUserId || attachmentsCount === null || !createdAt || !updatedAt
  ) {
    return null;
  }
  return {
    id, referenceCode, status, amountCents, approvedAmountCents: approved, category, occurredAt, description, vendor, paidBy,
    submittedByUserId, attachmentsCount, createdAt, updatedAt,
  };
}

function parseAttachment(v: unknown): Attachment | null {
  if (!isRecord(v)) return null;
  const id = str(v.id);
  const kind = oneOf(KINDS, v.kind);
  const fileName = str(v.file_name);
  const contentType = str(v.content_type);
  const sizeBytes = int(v.size_bytes);
  const uploadedByUserId = str(v.uploaded_by_user_id);
  const createdAt = str(v.created_at);
  return id && kind && fileName && contentType && sizeBytes !== null && uploadedByUserId && createdAt
    ? { id, kind, fileName, contentType, sizeBytes, uploadedByUserId, createdAt }
    : null;
}

function parseReimbursement(v: unknown): Reimbursement | null {
  if (!isRecord(v)) return null;
  const id = str(v.id);
  const status = oneOf(["PENDING", "PAID", "CANCELLED"] as const, v.status);
  const amountCents = int(v.amount_cents);
  const beneficiaryUserId = str(v.beneficiary_user_id);
  const paidByUserId = optStr(v.paid_by_user_id);
  const paymentReference = optStr(v.payment_reference);
  const settledAt = optStr(v.settled_at);
  const createdAt = str(v.created_at);
  if (!id || !status || amountCents === null || !beneficiaryUserId || paidByUserId === undefined || paymentReference === undefined || settledAt === undefined || !createdAt) return null;
  return { id, status, amountCents, beneficiaryUserId, paidByUserId, paymentReference, settledAt, createdAt };
}

export function parseExpenseDetail(v: unknown): ExpenseDetail | null {
  const base = parseSummary(v);
  if (!base || !isRecord(v) || !Array.isArray(v.attachments)) return null;
  const attachments: Attachment[] = [];
  for (const raw of v.attachments) {
    const a = parseAttachment(raw);
    if (!a) return null;
    attachments.push(a);
  }
  let reimbursement: Reimbursement | null = null;
  if (v.reimbursement !== null && v.reimbursement !== undefined) {
    reimbursement = parseReimbursement(v.reimbursement);
    if (!reimbursement) return null;
  }
  const purchaseReason = optStr(v.purchase_reason);
  const paymentMethod = v.payment_method === null || v.payment_method === undefined ? null : oneOf(METHODS, v.payment_method);
  const approvedByUserId = optStr(v.approved_by_user_id);
  const approvedAt = optStr(v.approved_at);
  const decisionReason = optStr(v.decision_reason);
  const correctionReason = optStr(v.correction_reason);
  const settledAt = optStr(v.settled_at);
  if ([purchaseReason, approvedByUserId, approvedAt, decisionReason, correctionReason, settledAt].some((x) => x === undefined)) return null;
  if (v.payment_method !== null && v.payment_method !== undefined && paymentMethod === null) return null;
  return {
    ...base,
    purchaseReason: purchaseReason as string | null,
    paymentMethod,
    approvedByUserId: approvedByUserId as string | null,
    approvedAt: approvedAt as string | null,
    decisionReason: decisionReason as string | null,
    correctionReason: correctionReason as string | null,
    settledAt: settledAt as string | null,
    attachments,
    reimbursement,
  };
}

function parsePage(body: unknown): Page<ExpenseSummary> | null {
  if (!isRecord(body) || !Array.isArray(body.items)) return null;
  const items: ExpenseSummary[] = [];
  for (const raw of body.items) {
    const item = parseSummary(raw);
    if (!item) return null;
    items.push(item);
  }
  const next = body.next_cursor;
  if (next !== null && next !== undefined && (typeof next !== "string" || next.length === 0 || next.length > 500)) return null;
  return { items, nextCursor: typeof next === "string" ? next : null };
}

const enc = encodeURIComponent;
const base = (schoolId: string) => `/api/v1/schools/${enc(schoolId)}`;
const detailOk = (body: unknown, status: number) => (status === 200 ? parseExpenseDetail(body) : null);

function query(params: Record<string, string | number | undefined>): string {
  const parts = Object.entries(params).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => `${k}=${enc(String(v))}`);
  return parts.length > 0 ? `?${parts.join("&")}` : "";
}

/** GET .../expense-categories: as categorias para o formulario. */
export function listCategories(schoolId: string, options?: RequestOptions): Promise<ApiResult<ExpenseCategory[]>> {
  return apiGet(
    `${base(schoolId)}/expense-categories`,
    (body, status) => {
      if (status !== 200 || !isRecord(body) || !Array.isArray(body.items)) return null;
      const items: ExpenseCategory[] = [];
      for (const raw of body.items) {
        const c = parseCategory(raw);
        if (!c) return null;
        items.push(c);
      }
      return items;
    },
    options,
  );
}

/** GET .../expenses: `read_all` ve a escola toda; os demais, so as proprias. Mais recentes primeiro. */
export function listExpenses(
  schoolId: string,
  params: { status?: ExpenseStatus; period?: string; cursor?: string; limit?: number } = {},
  options?: RequestOptions,
): Promise<ApiResult<Page<ExpenseSummary>>> {
  return apiGet(
    `${base(schoolId)}/expenses${query({ status: params.status, period: params.period, cursor: params.cursor, limit: params.limit })}`,
    (body, status) => (status === 200 ? parsePage(body) : null),
    options,
  );
}

export function getExpense(schoolId: string, expenseId: string, options?: RequestOptions): Promise<ApiResult<ExpenseDetail>> {
  return apiGet(`${base(schoolId)}/expenses/${enc(expenseId)}`, detailOk, options);
}

/** Corpo snake_case: so os campos presentes (o PATCH muda so o que veio). `paid_by` so existe na criacao. */
export function expenseBody(input: ExpenseUpdate & { paidBy?: PaidBy }): Record<string, string | number> {
  const body: Record<string, string | number> = {};
  if (input.amountCents !== undefined) body.amount_cents = input.amountCents;
  if (input.occurredAt !== undefined) body.occurred_at = input.occurredAt;
  if (input.categoryId !== undefined) body.category_id = input.categoryId;
  if (input.description !== undefined) body.description = input.description;
  if (input.vendor) body.vendor = input.vendor;
  if (input.purchaseReason) body.purchase_reason = input.purchaseReason;
  if (input.paymentMethod) body.payment_method = input.paymentMethod;
  if (input.paidBy) body.paid_by = input.paidBy;
  return body;
}

/** POST .../expenses: cria um RASCUNHO. Quem envia e o autor (vem da sessao; o nome nunca e pedido). */
export function createExpense(schoolId: string, input: ExpenseInput, options?: SendOptions): Promise<ApiResult<ExpenseDetail>> {
  return apiSend("POST", `${base(schoolId)}/expenses`, expenseBody(input), (b, s) => (s === 201 || s === 200 ? parseExpenseDetail(b) : null), options);
}

/** PATCH .../expenses/{id}: so o autor, so em rascunho ou correcao pedida. */
export function updateExpense(schoolId: string, expenseId: string, input: ExpenseUpdate, options?: SendOptions): Promise<ApiResult<ExpenseDetail>> {
  return apiSend("PATCH", `${base(schoolId)}/expenses/${enc(expenseId)}`, expenseBody(input), detailOk, options);
}

export interface Uploaded {
  attachment: Attachment;
}

/** POST .../attachments (multipart): so o autor, so em rascunho ou correcao pedida (`409 attachments_closed`). */
export function uploadAttachment(
  schoolId: string,
  expenseId: string,
  file: File,
  kind: AttachmentKind = "INVOICE",
  options?: SendOptions,
): Promise<ApiResult<Attachment>> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("kind", kind);
  return apiUpload(`${base(schoolId)}/expenses/${enc(expenseId)}/attachments`, form, (body, status) => (status === 201 || status === 200 ? parseAttachment(body) : null), options);
}

/** GET .../attachments/{id}: o arquivo pela API, com a sessao. Nunca uma URL publica. */
export function downloadAttachment(schoolId: string, expenseId: string, attachmentId: string, options?: RequestOptions): Promise<ApiResult<BlobPayload>> {
  return apiBlob(`${base(schoolId)}/expenses/${enc(expenseId)}/attachments/${enc(attachmentId)}`, options);
}

const action = (schoolId: string, expenseId: string, name: string, body: unknown, options?: SendOptions) =>
  apiSend("POST", `${base(schoolId)}/expenses/${enc(expenseId)}/${name}`, body, detailOk, options);

export const submitExpense = (schoolId: string, expenseId: string, options?: SendOptions) => action(schoolId, expenseId, "submit", undefined, options);
export const cancelExpense = (schoolId: string, expenseId: string, options?: SendOptions) => action(schoolId, expenseId, "cancel", undefined, options);
export const payExpense = (schoolId: string, expenseId: string, options?: SendOptions) => action(schoolId, expenseId, "pay", undefined, options);

/** Aprova pelo valor pedido (sem corpo) ou por um valor MENOR (so quando um colaborador pagou), com motivo. */
export function approveExpense(
  schoolId: string,
  expenseId: string,
  input: { approvedAmountCents?: number; reason?: string } = {},
  options?: SendOptions,
): Promise<ApiResult<ExpenseDetail>> {
  const body: Record<string, string | number> = {};
  if (input.approvedAmountCents !== undefined) body.approved_amount_cents = input.approvedAmountCents;
  if (input.reason) body.reason = input.reason;
  return action(schoolId, expenseId, "approve", Object.keys(body).length > 0 ? body : undefined, options);
}

export const rejectExpense = (schoolId: string, expenseId: string, reason: string, options?: SendOptions) => action(schoolId, expenseId, "reject", { reason }, options);
export const requestCorrection = (schoolId: string, expenseId: string, reason: string, options?: SendOptions) => action(schoolId, expenseId, "request-correction", { reason }, options);
export const reimburseExpense = (schoolId: string, expenseId: string, paymentReference: string, options?: SendOptions) =>
  action(schoolId, expenseId, "reimburse", { payment_reference: paymentReference }, options);
