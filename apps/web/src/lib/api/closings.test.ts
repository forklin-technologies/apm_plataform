import { describe, expect, it, vi } from "vitest";
import { json, problem } from "@/test-utils/auth-fixtures";
import { closingBody } from "@/test-utils/expense-fixtures";
import { SCHOOL_ID } from "@/test-utils/statement-fixtures";
import { createClosing, downloadClosingPdf, listClosings, parseClosing, reopenClosing, verifyClosing } from "./closings";

const ok = (body: unknown, status = 200) => vi.fn().mockResolvedValue(json(body, status, { "Content-Type": "application/json" }));
const csrf = "c".repeat(32);
type Init = RequestInit & { headers: Record<string, string> };
const init = (f: ReturnType<typeof vi.fn>) => f.mock.calls[0]![1] as Init;
const url = (f: ReturnType<typeof vi.fn>) => f.mock.calls[0]![0] as string;
const C = "9a560fdf-0a78-482f-8c74-9965db69cdad";

describe("closings", () => {
  it("lista com os reabertos; snake_case vira camelCase com os dois saldos, o codigo e a diferenca do banco", async () => {
    const f = ok({ items: [closingBody(), closingBody({ id: "x", period: "2025-12", reopened_at: "2026-10-06T16:00:00Z", reopen_reason: "Erro de lancamento" })], next_cursor: null });
    const result = await listClosings(SCHOOL_ID, { limit: 24 }, { fetchImpl: f });
    expect(url(f)).toBe(`/api/v1/schools/${SCHOOL_ID}/closings?include_reopened=true&limit=24`);
    expect(result).toMatchObject({
      ok: true,
      data: {
        nextCursor: null,
        items: [
          { period: "2026-01", openingBalanceCents: 10000, closingBalanceCents: 34500, closingAfterPendingCents: 30000, bankBalanceReportedCents: 35000, bankDifferenceCents: 500, entriesCount: 7, reopenedAt: null, entriesHash: expect.stringMatching(/^[0-9a-f]{64}$/) },
          { period: "2025-12", reopenedAt: "2026-10-06T16:00:00Z", reopenReason: "Erro de lancamento" },
        ],
      },
    });
  });

  it("viewer: campos de quem fechou/reabriu e o motivo podem vir nulos; banco nao informado", () => {
    const c = parseClosing(closingBody({ bank_balance_reported_cents: null, bank_difference_cents: null, closed_by_user_id: null }));
    expect(c).toMatchObject({ bankBalanceReportedCents: null, bankDifferenceCents: null, reopenReason: null });
  });

  it("recusa fechamento mal formado", () => {
    expect(parseClosing(closingBody({ period: "janeiro" }))).toBeNull();
    expect(parseClosing(closingBody({ closing_balance_cents: 1.5 }))).toBeNull();
    expect(parseClosing(closingBody({ entries_hash: "" }))).toBeNull();
    expect(parseClosing(null)).toBeNull();
  });

  it("fechar: POST com o mes e o saldo do banco em centavos (opcional)", async () => {
    const f = ok(closingBody(), 201);
    const result = await createClosing(SCHOOL_ID, { period: "2026-01", bankBalanceReportedCents: 35000 }, { fetchImpl: f, csrfToken: csrf });
    expect(url(f)).toBe(`/api/v1/schools/${SCHOOL_ID}/closings`);
    expect(init(f).method).toBe("POST");
    expect(init(f).headers["X-CSRF-Token"]).toBe(csrf);
    expect(JSON.parse(init(f).body as string)).toEqual({ period: "2026-01", bank_balance_reported_cents: 35000 });
    expect(result.ok).toBe(true);
    const g = ok(closingBody(), 201);
    await createClosing(SCHOOL_ID, { period: "2026-01" }, { fetchImpl: g, csrfToken: csrf });
    expect(JSON.parse(init(g).body as string)).toEqual({ period: "2026-01" });
  });

  it("fechar fora de ordem: o erro traz so o mes que a API nomeia (nunca o texto do servidor)", async () => {
    const r = await createClosing(SCHOOL_ID, { period: "2026-05" }, {
      fetchImpl: vi.fn().mockResolvedValue(problem("closing_out_of_sequence", 409, { detail: "The next month to close is 2026-02" })),
    });
    expect(r).toEqual({ ok: false, error: { kind: "problem", status: 409, code: "closing_out_of_sequence", detailPeriod: "2026-02" } });
    expect(JSON.stringify(r)).not.toMatch(/next month|Texto em ingles/);
    const none = await createClosing(SCHOOL_ID, { period: "2026-05" }, { fetchImpl: vi.fn().mockResolvedValue(problem("closing_out_of_sequence", 409, { detail: "<script>2026-13</script>" })) });
    expect(none).toMatchObject({ error: { code: "closing_out_of_sequence" } });
    expect((none as { error: { detailPeriod?: string } }).error.detailPeriod).toBeUndefined();
    const other = await createClosing(SCHOOL_ID, { period: "2026-05" }, { fetchImpl: vi.fn().mockResolvedValue(problem("period_not_ended", 409, { detail: "2026-10" })) });
    expect((other as { error: { detailPeriod?: string } }).error.detailPeriod).toBeUndefined();
  });

  it("verificar e reabrir", async () => {
    const v = ok({ closing_id: C, verified: true, entries_hash: "a".repeat(64) });
    expect(await verifyClosing(SCHOOL_ID, C, { fetchImpl: v, csrfToken: csrf })).toEqual({ ok: true, data: { closingId: C, verified: true, entriesHash: "a".repeat(64) } });
    expect(url(v)).toBe(`/api/v1/schools/${SCHOOL_ID}/closings/${C}/verify`);
    expect(init(v).body).toBeUndefined();

    const r = ok(closingBody({ reopened_at: "2026-10-06T16:00:00Z", reopen_reason: "Motivo de teste do site" }));
    const reopened = await reopenClosing(SCHOOL_ID, C, "Motivo de teste do site", { fetchImpl: r, csrfToken: csrf });
    expect(url(r)).toBe(`/api/v1/schools/${SCHOOL_ID}/closings/${C}/reopen`);
    expect(JSON.parse(init(r).body as string)).toEqual({ reason: "Motivo de teste do site" });
    expect(reopened).toMatchObject({ ok: true, data: { reopenedAt: "2026-10-06T16:00:00Z" } });
    expect(await reopenClosing(SCHOOL_ID, C, "x".repeat(10), { fetchImpl: vi.fn().mockResolvedValue(problem("permission_denied", 403)) })).toMatchObject({ error: { status: 403 } });
  });

  it("PDF: pela API (Blob); reaberto = 409 closing_reopened", async () => {
    const f = vi.fn().mockResolvedValue(new Response("%PDF-1.4 x", { status: 200, headers: { "Content-Type": "application/pdf" } }));
    const result = await downloadClosingPdf(SCHOOL_ID, C, { fetchImpl: f });
    expect(url(f)).toBe(`/api/v1/schools/${SCHOOL_ID}/closings/${C}/report.pdf`);
    expect(result.ok && result.data.contentType).toBe("application/pdf");
    expect(result.ok && (await result.data.blob.text()).startsWith("%PDF")).toBe(true);
    expect(await downloadClosingPdf(SCHOOL_ID, C, { fetchImpl: vi.fn().mockResolvedValue(problem("closing_reopened", 409)) })).toMatchObject({ ok: false, error: { status: 409, code: "closing_reopened" } });
  });
});
