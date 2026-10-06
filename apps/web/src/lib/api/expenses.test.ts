import { describe, expect, it, vi } from "vitest";
import { json, problem } from "@/test-utils/auth-fixtures";
import { attachmentBody, detailBody, summaryBody } from "@/test-utils/expense-fixtures";
import { SCHOOL_ID } from "@/test-utils/statement-fixtures";
import {
  approveExpense, cancelExpense, createExpense, downloadAttachment, expenseBody, getExpense, listCategories, listExpenses,
  payExpense, reimburseExpense, rejectExpense, requestCorrection, submitExpense, updateExpense, uploadAttachment,
} from "./expenses";

const ok = (body: unknown, status = 200) => vi.fn().mockResolvedValue(json(body, status, { "Content-Type": "application/json" }));
const csrf = "c".repeat(32);
type Init = RequestInit & { headers: Record<string, string> };
const init = (f: ReturnType<typeof vi.fn>): Init => f.mock.calls[0]![1] as Init;
const url = (f: ReturnType<typeof vi.fn>) => f.mock.calls[0]![0] as string;
const E = "405ce5fe-8bdb-4899-95a8-3a4295b28d16";

describe("leitura", () => {
  it("categorias e lista (status, mes, cursor, limite) em camelCase; a lista traz o id de quem enviou", async () => {
    const cats = ok({ items: [{ id: "c1", key: "school_supplies", name: "Compra de material" }] });
    expect(await listCategories(SCHOOL_ID, { fetchImpl: cats })).toEqual({ ok: true, data: [{ id: "c1", key: "school_supplies", name: "Compra de material" }] });
    expect(url(cats)).toBe(`/api/v1/schools/${SCHOOL_ID}/expense-categories`);

    const list = ok({ items: [summaryBody()], next_cursor: "c2" });
    const result = await listExpenses(SCHOOL_ID, { status: "SUBMITTED", period: "2026-10", cursor: "x", limit: 50 }, { fetchImpl: list });
    expect(url(list)).toBe(`/api/v1/schools/${SCHOOL_ID}/expenses?status=SUBMITTED&period=2026-10&cursor=x&limit=50`);
    expect(result).toMatchObject({
      ok: true,
      data: { nextCursor: "c2", items: [{ referenceCode: 24, status: "SUBMITTED", amountCents: 20000, approvedAmountCents: null, paidBy: "COLLABORATOR", category: { key: "school_supplies" }, submittedByUserId: expect.any(String), attachmentsCount: 1 }] },
    });
  });

  it("detalhe: anexos, motivos e reembolso", async () => {
    const f = ok(detailBody({
      status: "CORRECTION_REQUESTED", correction_reason: "Falta o CNPJ na nota", decision_reason: null,
      reimbursement: { id: "r1", status: "PAID", amount_cents: 17500, beneficiary_user_id: "u1", paid_by_user_id: "u2", payment_reference: "PIX 123", settled_at: "2026-10-06T16:00:00Z", created_at: "2026-10-06T15:00:00Z" },
    }));
    const result = await getExpense(SCHOOL_ID, E, { fetchImpl: f });
    expect(url(f)).toBe(`/api/v1/schools/${SCHOOL_ID}/expenses/${E}`);
    expect(result).toMatchObject({
      ok: true,
      data: { status: "CORRECTION_REQUESTED", correctionReason: "Falta o CNPJ na nota", purchaseReason: "Aula de artes", paymentMethod: "PIX", attachments: [{ fileName: "nota.pdf", kind: "INVOICE", sizeBytes: 20480 }], reimbursement: { amountCents: 17500, paymentReference: "PIX 123" } },
    });
  });

  it("recusa estado, tipo, centavos ou anexo mal formados (nada de adivinhar)", async () => {
    for (const bad of [
      summaryBody({ status: "DONE" }), summaryBody({ amount_cents: 10.5 }), summaryBody({ paid_by: "BANK" }), summaryBody({ category: {} }),
    ]) {
      expect((await listExpenses(SCHOOL_ID, {}, { fetchImpl: ok({ items: [bad], next_cursor: null }) })).ok).toBe(false);
    }
    expect((await getExpense(SCHOOL_ID, E, { fetchImpl: ok(detailBody({ attachments: [attachmentBody({ kind: "SELFIE" })] })) })).ok).toBe(false);
    expect((await getExpense(SCHOOL_ID, E, { fetchImpl: ok(detailBody({ payment_method: "BOLETO" })) })).ok).toBe(false);
    expect((await listExpenses(SCHOOL_ID, {}, { fetchImpl: ok({ items: [], next_cursor: 5 }) })).ok).toBe(false);
  });

  it("403 e 404 viram erros com code", async () => {
    expect(await getExpense(SCHOOL_ID, E, { fetchImpl: vi.fn().mockResolvedValue(problem("not_found", 404)) })).toMatchObject({ error: { status: 404, code: "not_found" } });
    expect(await listExpenses(SCHOOL_ID, {}, { fetchImpl: vi.fn().mockResolvedValue(problem("permission_denied", 403)) })).toMatchObject({ error: { status: 403 } });
  });
});

describe("escrita", () => {
  it("criar: POST com corpo snake_case, sem nome de pessoa e sem campos vazios; 201 vira rascunho", async () => {
    const f = ok(detailBody({ status: "DRAFT" }), 201);
    const result = await createExpense(
      SCHOOL_ID,
      { amountCents: 1500, occurredAt: "2026-10-06", categoryId: "c1", description: "Cola", paidBy: "COLLABORATOR", vendor: "", purchaseReason: "Aula", paymentMethod: "PIX" },
      { fetchImpl: f, csrfToken: csrf },
    );
    expect(url(f)).toBe(`/api/v1/schools/${SCHOOL_ID}/expenses`);
    expect(init(f).method).toBe("POST");
    expect(init(f).headers["X-CSRF-Token"]).toBe(csrf);
    expect(JSON.parse(init(f).body as string)).toEqual({
      amount_cents: 1500, occurred_at: "2026-10-06", category_id: "c1", description: "Cola", purchase_reason: "Aula", payment_method: "PIX", paid_by: "COLLABORATOR",
    });
    expect(result.ok).toBe(true);
  });

  it("editar: PATCH so com os campos presentes e NUNCA paid_by", async () => {
    expect(expenseBody({ description: "Nova" })).toEqual({ description: "Nova" });
    const f = ok(detailBody());
    await updateExpense(SCHOOL_ID, E, { amountCents: 900 }, { fetchImpl: f, csrfToken: csrf });
    expect(init(f).method).toBe("PATCH");
    expect(JSON.parse(init(f).body as string)).toEqual({ amount_cents: 900 });
  });

  it("anexar: multipart com file e kind; sem Content-Type manual (o navegador poe o boundary); conteudo so no corpo", async () => {
    const f = ok(attachmentBody(), 201);
    const file = new File(["%PDF-1.4 conteudo secreto"], "nota.pdf", { type: "application/pdf" });
    const result = await uploadAttachment(SCHOOL_ID, E, file, "INVOICE", { fetchImpl: f, csrfToken: csrf });
    expect(url(f)).toBe(`/api/v1/schools/${SCHOOL_ID}/expenses/${E}/attachments`);
    expect(url(f)).not.toContain("nota");
    const i = init(f);
    expect(i.method).toBe("POST");
    expect(i.body).toBeInstanceOf(FormData);
    expect((i.body as FormData).get("kind")).toBe("INVOICE");
    expect(((i.body as FormData).get("file") as File).name).toBe("nota.pdf");
    expect(i.headers["Content-Type"]).toBeUndefined();
    expect(i.headers["X-CSRF-Token"]).toBe(csrf);
    expect(result).toMatchObject({ ok: true, data: { fileName: "nota.pdf", kind: "INVOICE" } });
  });

  it("anexar: 409 attachments_closed, 413 e 415 viram erros", async () => {
    const file = new File(["x"], "n.pdf");
    for (const [code, status] of [["attachments_closed", 409], ["payload_too_large", 413], ["attachment_type_not_allowed", 415]] as const) {
      const r = await uploadAttachment(SCHOOL_ID, E, file, "OTHER", { fetchImpl: vi.fn().mockResolvedValue(problem(code, status)) });
      expect(r).toMatchObject({ ok: false, error: { status, code } });
    }
  });

  it("baixar anexo: GET pela API; devolve o Blob e o tipo; erro vem como problem", async () => {
    const f = vi.fn().mockResolvedValue(new Response("%PDF-1.4 abc", { status: 200, headers: { "Content-Type": "application/pdf" } }));
    const result = await downloadAttachment(SCHOOL_ID, E, "a1", { fetchImpl: f });
    expect(url(f)).toBe(`/api/v1/schools/${SCHOOL_ID}/expenses/${E}/attachments/a1`);
    expect(init(f).method).toBe("GET");
    expect(result.ok && result.data.contentType).toBe("application/pdf");
    expect(result.ok && (await result.data.blob.text())).toBe("%PDF-1.4 abc");
    expect(await downloadAttachment(SCHOOL_ID, E, "a1", { fetchImpl: vi.fn().mockResolvedValue(problem("not_found", 404)) })).toMatchObject({ ok: false, error: { status: 404, code: "not_found" } });
  });

  it("acoes sem corpo: submit, cancel e pay", async () => {
    for (const [fn, name] of [[submitExpense, "submit"], [cancelExpense, "cancel"], [payExpense, "pay"]] as const) {
      const f = ok(detailBody());
      await fn(SCHOOL_ID, E, { fetchImpl: f, csrfToken: csrf });
      expect(url(f)).toBe(`/api/v1/schools/${SCHOOL_ID}/expenses/${E}/${name}`);
      expect(init(f).method).toBe("POST");
      expect(init(f).body).toBeUndefined();
    }
  });

  it("aprovar: sem corpo (valor pedido) ou com valor menor e motivo; recusar e pedir correcao levam o motivo; reembolso leva a referencia", async () => {
    const full = ok(detailBody({ status: "APPROVED" }));
    await approveExpense(SCHOOL_ID, E, {}, { fetchImpl: full, csrfToken: csrf });
    expect(init(full).body).toBeUndefined();

    const partial = ok(detailBody({ status: "APPROVED" }));
    await approveExpense(SCHOOL_ID, E, { approvedAmountCents: 17500, reason: "Sem o frete" }, { fetchImpl: partial, csrfToken: csrf });
    expect(url(partial)).toBe(`/api/v1/schools/${SCHOOL_ID}/expenses/${E}/approve`);
    expect(JSON.parse(init(partial).body as string)).toEqual({ approved_amount_cents: 17500, reason: "Sem o frete" });

    const reject = ok(detailBody({ status: "REJECTED" }));
    await rejectExpense(SCHOOL_ID, E, "Não é da APM", { fetchImpl: reject, csrfToken: csrf });
    expect(url(reject)).toMatch(/\/reject$/);
    expect(JSON.parse(init(reject).body as string)).toEqual({ reason: "Não é da APM" });

    const corr = ok(detailBody({ status: "CORRECTION_REQUESTED" }));
    await requestCorrection(SCHOOL_ID, E, "Falta o CNPJ", { fetchImpl: corr, csrfToken: csrf });
    expect(url(corr)).toMatch(/\/request-correction$/);
    expect(JSON.parse(init(corr).body as string)).toEqual({ reason: "Falta o CNPJ" });

    const reimb = ok(detailBody({ status: "PAID" }));
    await reimburseExpense(SCHOOL_ID, E, "PIX 123", { fetchImpl: reimb, csrfToken: csrf });
    expect(url(reimb)).toMatch(/\/reimburse$/);
    expect(JSON.parse(init(reimb).body as string)).toEqual({ payment_reference: "PIX 123" });
  });

  it("403 do autor, 409 de estado e 422 de aprovacao parcial viram erros com code", async () => {
    for (const [code, status] of [["self_approval_forbidden", 403], ["invalid_state", 409], ["partial_approval_not_allowed", 422], ["self_payment_forbidden", 403]] as const) {
      const r = await approveExpense(SCHOOL_ID, E, {}, { fetchImpl: vi.fn().mockResolvedValue(problem(code, status)) });
      expect(r).toMatchObject({ ok: false, error: { status, code } });
    }
  });

  it("o conteudo do anexo e a Idempotency-Key nunca aparecem no console", async () => {
    const spies = [vi.spyOn(console, "error"), vi.spyOn(console, "warn"), vi.spyOn(console, "log")];
    await uploadAttachment(SCHOOL_ID, E, new File(["segredo"], "n.pdf"), "INVOICE", { fetchImpl: vi.fn().mockRejectedValue(new TypeError("x")) });
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });
});
