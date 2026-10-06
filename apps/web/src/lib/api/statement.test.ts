import { describe, expect, it, vi } from "vitest";
import { json, problem } from "@/test-utils/auth-fixtures";
import { SCHOOL_ID, SUMMARY_BODY, entryBody, pendingBody } from "@/test-utils/statement-fixtures";
import { contributionsPdfHref, getPending, getStatement, getSummary, parseSummary, recordCashContribution } from "./statement";

const ok = (body: unknown, status = 200) => vi.fn().mockResolvedValue(json(body, status, { "Content-Type": "application/json" }));
const KEY = "11111111-1111-4111-8111-111111111111";

describe("getSummary", () => {
  it("GET .../statement/summary?period=: snake_case vira camelCase, com os DOIS saldos", async () => {
    const fetchImpl = ok(SUMMARY_BODY);
    const result = await getSummary(SCHOOL_ID, "2026-10", { fetchImpl });
    expect(fetchImpl.mock.calls[0]![0]).toBe(`/api/v1/schools/${SCHOOL_ID}/statement/summary?period=2026-10`);
    expect(result).toMatchObject({
      ok: true,
      data: { period: "2026-10", timezone: "America/Sao_Paulo", closingBalanceCents: 68410, balanceAfterPendingCents: 63910, pendingReimbursementsCents: 4500, entriesCount: 14 },
    });
  });

  it("sem period, nao manda o parametro (a API usa o mes corrente da escola)", async () => {
    const fetchImpl = ok(SUMMARY_BODY);
    await getSummary(SCHOOL_ID, undefined, { fetchImpl });
    expect(fetchImpl.mock.calls[0]![0]).toBe(`/api/v1/schools/${SCHOOL_ID}/statement/summary`);
  });

  it("recusa centavos nao inteiros e campos que faltam; o id da escola e codificado", async () => {
    expect(parseSummary({ ...SUMMARY_BODY, closing_balance_cents: 10.5 })).toBeNull();
    expect(parseSummary({ ...SUMMARY_BODY, period: "outubro" })).toBeNull();
    const { entries_count: _omit, ...missing } = SUMMARY_BODY;
    void _omit;
    expect(parseSummary(missing)).toBeNull();
    const fetchImpl = ok(SUMMARY_BODY);
    await getSummary("a/b", undefined, { fetchImpl });
    expect(fetchImpl.mock.calls[0]![0]).toBe("/api/v1/schools/a%2Fb/statement/summary");
  });

  it("403 (perfil sem acesso), 404 (escola de outro vinculo) e 422 viram erros com code", async () => {
    for (const [code, status] of [["permission_denied", 403], ["not_found", 404], ["validation_error", 422]] as const) {
      const result = await getSummary(SCHOOL_ID, "2026-10", { fetchImpl: vi.fn().mockResolvedValue(problem(code, status)) });
      expect(result).toMatchObject({ ok: false, error: { status, code } });
    }
  });
});

describe("getStatement e getPending", () => {
  it("lancamentos: query com period, kind, cursor e limit; pagina com next_cursor", async () => {
    const fetchImpl = ok({ items: [entryBody()], next_cursor: "abc123" });
    const result = await getStatement(SCHOOL_ID, { period: "2026-10", kind: "CONTRIBUTION", limit: 50, cursor: "xyz" }, { fetchImpl });
    expect(fetchImpl.mock.calls[0]![0]).toBe(`/api/v1/schools/${SCHOOL_ID}/statement?period=2026-10&kind=CONTRIBUTION&cursor=xyz&limit=50`);
    expect(result).toMatchObject({
      ok: true,
      data: {
        nextCursor: "abc123",
        items: [{ referenceCode: 21, kind: "CONTRIBUTION", direction: "IN", amountCents: 3000, signedAmountCents: 3000, localDate: "2026-10-06", originLabel: "Maria Teste", categoryKey: "parent_contribution", runningBalanceCents: 42710 }],
      },
    });
  });

  it("ultima pagina: next_cursor nulo", async () => {
    const result = await getStatement(SCHOOL_ID, {}, { fetchImpl: ok({ items: [], next_cursor: null }) });
    expect(result).toEqual({ ok: true, data: { items: [], nextCursor: null } });
  });

  it("recusa lancamento mal formado, tipo desconhecido e cursor estranho (nada de adivinhar dinheiro)", async () => {
    for (const bad of [
      { items: [entryBody({ kind: "GIFT" })], next_cursor: null },
      { items: [entryBody({ amount_cents: "3000" })], next_cursor: null },
      { items: [entryBody({ direction: "SIDEWAYS" })], next_cursor: null },
      { items: [entryBody({ display_type: "OTHER" })], next_cursor: null },
      { items: [entryBody()], next_cursor: 5 },
      { items: "x", next_cursor: null },
    ]) {
      expect((await getStatement(SCHOOL_ID, {}, { fetchImpl: ok(bad) })).ok).toBe(false);
    }
  });

  it("pendencias: GET .../statement/pending com limit", async () => {
    const fetchImpl = ok({ items: [pendingBody()], next_cursor: null });
    const result = await getPending(SCHOOL_ID, { limit: 100 }, { fetchImpl });
    expect(fetchImpl.mock.calls[0]![0]).toBe(`/api/v1/schools/${SCHOOL_ID}/statement/pending?limit=100`);
    expect(result).toMatchObject({ ok: true, data: { items: [{ referenceCode: 16, kind: "REIMBURSEMENT", section: "PAYABLE", amountCents: 4500 }] } });
  });

  it("viewer: 403 permission_denied nas duas rotas", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(problem("permission_denied", 403));
    expect(await getStatement(SCHOOL_ID, {}, { fetchImpl })).toMatchObject({ error: { status: 403, code: "permission_denied" } });
    expect(await getPending(SCHOOL_ID, {}, { fetchImpl })).toMatchObject({ error: { status: 403 } });
  });
});

describe("recordCashContribution e PDF", () => {
  it("POST com Idempotency-Key no cabecalho, corpo em snake_case e so campos preenchidos", async () => {
    const fetchImpl = ok({ id: "i", reference_code: "APM-000042", status: "PAID", amount_cents: 5000, method: "CASH" }, 201);
    const result = await recordCashContribution(
      SCHOOL_ID,
      { amountCents: 5000, method: "CASH", categoryKey: "parent_contribution", identification: { guardianName: "João", classroom: "" } },
      KEY,
      { fetchImpl, csrfToken: "c".repeat(32) },
    );
    const [url, init] = fetchImpl.mock.calls[0]! as [string, RequestInit & { headers: Record<string, string> }];
    expect(url).toBe(`/api/v1/schools/${SCHOOL_ID}/contributions`);
    expect(url).not.toContain(KEY);
    expect(init.headers["Idempotency-Key"]).toBe(KEY);
    expect(init.headers["X-CSRF-Token"]).toBe("c".repeat(32));
    expect(JSON.parse(init.body as string)).toEqual({ amount_cents: 5000, method: "CASH", category_key: "parent_contribution", guardian_name: "João" });
    expect(result).toEqual({ ok: true, data: { id: "i", referenceCode: "APM-000042", status: "PAID", amountCents: 5000, method: "CASH" } });
  });

  it("200 (repeticao com a mesma chave) tambem e sucesso; 403 e 422 viram erros", async () => {
    const replay = await recordCashContribution(SCHOOL_ID, { amountCents: 1, method: "CASH", categoryKey: "donation", identification: {} }, KEY, {
      fetchImpl: ok({ id: "i", reference_code: "APM-000042", status: "PAID", amount_cents: 1, method: "CASH" }, 200),
    });
    expect(replay.ok).toBe(true);
    const denied = await recordCashContribution(SCHOOL_ID, { amountCents: 1, method: "CASH", categoryKey: "donation", identification: {} }, KEY, {
      fetchImpl: vi.fn().mockResolvedValue(problem("permission_denied", 403)),
    });
    expect(denied).toMatchObject({ error: { status: 403, code: "permission_denied" } });
  });

  it("contributionsPdfHref: link do PDF do mes (navegacao, nao fetch)", () => {
    expect(contributionsPdfHref(SCHOOL_ID, "2026-10")).toBe(`/api/v1/schools/${SCHOOL_ID}/reports/contributions.pdf?period=2026-10`);
  });
});
