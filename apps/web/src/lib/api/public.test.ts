import { describe, expect, it, vi } from "vitest";
import { RECEIPT_BODY, SCHOOL_BODY, TOKEN, TXID, stateBody } from "@/test-utils/public-fixtures";
import { json, problem } from "@/test-utils/auth-fixtures";
import {
  contributionBody,
  createContribution,
  getContribution,
  getPublicSchool,
  getReceipt,
  renewCharge,
  sandboxPay,
  sandboxTxid,
} from "./public";

const KEY = "11111111-1111-4111-8111-111111111111";
const ok = (body: unknown, status = 200) => vi.fn().mockResolvedValue(json(body, status, { "Content-Type": "application/json" }));
const init = (fetchImpl: ReturnType<typeof vi.fn>, call = 0) => fetchImpl.mock.calls[call]![1] as RequestInit & { headers: Record<string, string> };

describe("getPublicSchool", () => {
  it("GET /api/v1/public/schools/{slug}: snake_case vira camelCase e campos ausentes viram OCULTOS", async () => {
    const fetchImpl = ok({ ...SCHOOL_BODY, identification: { guardian_name: "REQUIRED", contributor_email: "OPTIONAL" } });
    const result = await getPublicSchool("demo-aurora", { fetchImpl });
    expect(fetchImpl.mock.calls[0]![0]).toBe("/api/v1/public/schools/demo-aurora");
    expect(init(fetchImpl).method).toBe("GET");
    expect(result).toMatchObject({
      ok: true,
      data: {
        slug: "demo-aurora",
        apmName: "APM da Escola Aurora (demo)",
        accentColor: "#0a84ff",
        suggestedAmountsCents: [2000, 3000, 4000],
        allowCustomAmount: true,
        minAmountCents: 1000,
        maxAmountCents: 500000,
        identification: {
          guardianName: "REQUIRED",
          studentName: "HIDDEN",
          classroom: "HIDDEN",
          contributorEmail: "OPTIONAL",
          contributorPhone: "HIDDEN",
        },
      },
    });
  });

  it("valor de regra estranho vira HIDDEN, nunca pede dado a mais", async () => {
    const result = await getPublicSchool("x", { fetchImpl: ok({ ...SCHOOL_BODY, identification: { guardian_name: "SOMETIMES" } }) });
    expect(result.ok && result.data.identification.guardianName).toBe("HIDDEN");
  });

  it("recusa corpo mal formado (cor, centavos, lista)", async () => {
    for (const bad of [
      { ...SCHOOL_BODY, accent_color: "azul" },
      { ...SCHOOL_BODY, min_amount_cents: 10.5 },
      { ...SCHOOL_BODY, suggested_amounts_cents: [0] },
      { ...SCHOOL_BODY, allow_custom_amount: "yes" },
      {},
    ]) {
      const result = await getPublicSchool("x", { fetchImpl: ok(bad) });
      expect(result).toMatchObject({ ok: false, error: { kind: "invalid-response" } });
    }
  });

  it("404 e 429 viram erros com code; o slug e codificado", async () => {
    const notFound = await getPublicSchool("a/b?c", { fetchImpl: vi.fn().mockResolvedValue(problem("not_found", 404)) });
    expect(notFound).toMatchObject({ ok: false, error: { status: 404, code: "not_found" } });
    const limited = await getPublicSchool("x", { fetchImpl: vi.fn().mockResolvedValue(problem("rate_limited", 429, {}, { "Retry-After": "20" })) });
    expect(limited).toMatchObject({ error: { code: "rate_limited", retryAfterSeconds: 20 } });
    const fetchImpl = ok(SCHOOL_BODY);
    await getPublicSchool("a/b?c", { fetchImpl });
    expect(fetchImpl.mock.calls[0]![0]).toBe("/api/v1/public/schools/a%2Fb%3Fc");
  });
});

describe("createContribution", () => {
  it("POST com Idempotency-Key no CABECALHO (nunca na URL), corpo so com o que foi preenchido e token validado", async () => {
    const fetchImpl = ok({ ...stateBody(), token: TOKEN }, 201);
    const result = await createContribution(
      "demo-aurora",
      { amountCents: 2000, identification: { guardianName: "Maria Teste", classroom: "", contributorEmail: "m@exemplo.com.br" } },
      KEY,
      { fetchImpl, csrfToken: null },
    );
    const [url] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/api/v1/public/schools/demo-aurora/contributions");
    expect(String(url)).not.toContain(KEY);
    expect(init(fetchImpl).method).toBe("POST");
    expect(init(fetchImpl).headers["Idempotency-Key"]).toBe(KEY);
    expect(JSON.parse(init(fetchImpl).body as string)).toEqual({
      amount_cents: 2000,
      guardian_name: "Maria Teste",
      contributor_email: "m@exemplo.com.br",
    });
    expect(result).toMatchObject({
      ok: true,
      data: { token: TOKEN, status: "PENDING_PAYMENT", amountCents: 2000, charge: { status: "PENDING", emvPayload: expect.stringContaining("PIX-SANDBOX:") } },
    });
  });

  it("contributionBody: nomes da API e nada de campos vazios", () => {
    expect(contributionBody({ amountCents: 500, identification: {} })).toEqual({ amount_cents: 500 });
    expect(contributionBody({ amountCents: 500, identification: { studentName: "Davi", classroom: "4B", contributorPhone: "11 91234-5678" } })).toEqual({
      amount_cents: 500,
      student_name: "Davi",
      class_name: "4B",
      contributor_phone: "11 91234-5678",
    });
  });

  it("recusa resposta sem token valido (nunca inventa um token)", async () => {
    for (const body of [stateBody(), { ...stateBody(), token: "curto" }, { ...stateBody(), token: "../../etc/passwd-aaaaaaaaaaaa" }]) {
      const result = await createContribution("x", { amountCents: 1, identification: {} }, KEY, { fetchImpl: ok(body, 201) });
      expect(result.ok).toBe(false);
    }
  });

  it("422 traz campo e code; 409, 429 e 501 viram erros com code", async () => {
    const r422 = await createContribution("x", { amountCents: 5, identification: {} }, KEY, {
      fetchImpl: vi.fn().mockResolvedValue(problem("validation_error", 422, { errors: [{ field: "amount_cents", code: "out_of_range" }] })),
    });
    expect(r422).toMatchObject({ error: { status: 422, code: "validation_error", fields: [{ field: "amount_cents", code: "out_of_range" }] } });
    for (const [code, status] of [["idempotency_key_reused", 409], ["rate_limited", 429], ["not_implemented", 501]] as const) {
      const r = await createContribution("x", { amountCents: 1, identification: {} }, KEY, { fetchImpl: vi.fn().mockResolvedValue(problem(code, status)) });
      expect(r).toMatchObject({ ok: false, error: { status, code } });
    }
  });
});

describe("estado, nova cobranca e comprovante", () => {
  it("GET .../charge: estado da contribuicao e da cobranca em camelCase; status desconhecido e invalido", async () => {
    const fetchImpl = ok(stateBody());
    const result = await getContribution("demo-aurora", TOKEN, { fetchImpl });
    expect(fetchImpl.mock.calls[0]![0]).toBe(`/api/v1/public/schools/demo-aurora/contributions/${TOKEN}/charge`);
    expect(result).toMatchObject({ ok: true, data: { status: "PENDING_PAYMENT", charge: { status: "PENDING", amountCents: 2000 } } });
    for (const weird of [stateBody({ status: "SETTLED" }), stateBody({}, { status: "CONFIRMED" }), stateBody({ amount_cents: -1 })]) {
      expect((await getContribution("x", TOKEN, { fetchImpl: ok(weird) })).ok).toBe(false);
    }
  });

  it("estado sem cobranca (charge null) e aceito", async () => {
    const result = await getContribution("x", TOKEN, { fetchImpl: ok(stateBody({ status: "CANCELLED" }, null)) });
    expect(result).toMatchObject({ ok: true, data: { status: "CANCELLED", charge: null } });
  });

  it("POST .../charges: sem corpo; 200 com o novo estado; 409 contribution_closed", async () => {
    const fetchImpl = ok(stateBody({}, { emv_payload: "PIX-SANDBOX:novo:2000" }));
    const result = await renewCharge("demo-aurora", TOKEN, { fetchImpl, csrfToken: null });
    expect(fetchImpl.mock.calls[0]![0]).toBe(`/api/v1/public/schools/demo-aurora/contributions/${TOKEN}/charges`);
    expect(init(fetchImpl).method).toBe("POST");
    expect(init(fetchImpl).body).toBeUndefined();
    expect(result).toMatchObject({ ok: true, data: { charge: { emvPayload: "PIX-SANDBOX:novo:2000" } } });
    const closed = await renewCharge("x", TOKEN, { fetchImpl: vi.fn().mockResolvedValue(problem("contribution_closed", 409)) });
    expect(closed).toMatchObject({ error: { status: 409, code: "contribution_closed" } });
  });

  it("receipt: 200 pago vira Receipt; 409 e 404 sao erros; corpo nao PAID e invalido", async () => {
    const fetchImpl = ok(RECEIPT_BODY);
    expect(await getReceipt("demo-aurora", TOKEN, { fetchImpl })).toMatchObject({
      ok: true,
      data: { status: "PAID", referenceCode: "APM-000026", amountCents: 2000, schoolName: "Escola Aurora (demo)", method: "PIX" },
    });
    expect(fetchImpl.mock.calls[0]![0]).toBe(`/api/v1/public/schools/demo-aurora/contributions/${TOKEN}/receipt`);
    expect(await getReceipt("x", TOKEN, { fetchImpl: vi.fn().mockResolvedValue(problem("payment_not_confirmed", 409)) })).toMatchObject({ error: { status: 409, code: "payment_not_confirmed" } });
    expect(await getReceipt("x", TOKEN, { fetchImpl: vi.fn().mockResolvedValue(problem("not_found", 404)) })).toMatchObject({ error: { status: 404 } });
    expect((await getReceipt("x", TOKEN, { fetchImpl: ok({ ...RECEIPT_BODY, status: "PENDING" }) })).ok).toBe(false);
  });
});

describe("sandbox (so desenvolvimento)", () => {
  it("sandboxTxid so existe para payload PIX-SANDBOX e com txid bem formado", () => {
    expect(sandboxTxid(`PIX-SANDBOX:${TXID}:2000`)).toBe(TXID);
    expect(sandboxTxid("00020126580014br.gov.bcb.pix0136abc")).toBeNull();
    expect(sandboxTxid(null)).toBeNull();
    expect(sandboxTxid("PIX-SANDBOX:curto:2000")).toBeNull();
    expect(sandboxTxid("PIX-SANDBOX:../../etc/passwd/xx:1")).toBeNull();
  });

  it("sandboxPay: POST /api/v1/dev/sandbox/pix/{txid}/pay", async () => {
    const fetchImpl = ok({ status: "PAID" });
    expect(await sandboxPay(TXID, { fetchImpl, csrfToken: null })).toEqual({ ok: true, data: { status: "PAID" } });
    expect(fetchImpl.mock.calls[0]![0]).toBe(`/api/v1/dev/sandbox/pix/${TXID}/pay`);
    expect(init(fetchImpl).method).toBe("POST");
    expect((await sandboxPay(TXID, { fetchImpl: vi.fn().mockResolvedValue(problem("not_found", 404)) })).ok).toBe(false);
  });
});

describe("a chave de idempotencia nunca vaza", () => {
  it("nao e escrita no console quando a rede falha", async () => {
    const spies = [vi.spyOn(console, "error"), vi.spyOn(console, "warn"), vi.spyOn(console, "log")];
    await createContribution("x", { amountCents: 1, identification: {} }, KEY, { fetchImpl: vi.fn().mockRejectedValue(new TypeError("x")) });
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });
});
