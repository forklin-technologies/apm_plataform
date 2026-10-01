import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CHARGE_TTL_MS, PAY_DELAY_MS, mockContributions, resetMockOrders } from "./contributions";

// A latencia simulada nao importa aqui; o relogio e controlado pelos fake timers.
vi.mock("./util", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./util")>()),
  delay: () => Promise.resolve(),
}));

const base = {
  slug: "escola-exemplo",
  identification: { guardianName: "Ana Lima", studentName: "Davi Lima" },
};

describe("mock de contribuicao e Pix", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-30T12:00:00-03:00"));
    resetMockOrders();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  async function createQuota() {
    const promise = mockContributions.create({ ...base, amount: { kind: "QUOTA", quotaId: "cota-anual" } });
    const result = await promise;
    if (!result.ok) throw new Error("create falhou");
    return result.data.charge;
  }

  it("cria a cobranca PENDING com valor da cota em centavos e payload obviamente falso", async () => {
    const charge = await createQuota();
    expect(charge.status).toBe("PENDING");
    expect(charge.amountCents).toBe(20000);
    expect(Number.isInteger(charge.amountCents)).toBe(true);
    expect(charge.payload).toMatch(/^PROTOTIPO\.NAO-E-PIX\.NAO-PAGUE\./);
    expect(charge.payload).not.toMatch(/000201|br\.gov\.bcb/i);
    expect(charge.paidAt).toBeNull();
    expect(new Date(charge.expiresAt).getTime() - new Date(charge.createdAt).getTime()).toBe(CHARGE_TTL_MS);
  });

  it("o status so muda para PAID quando a camada de dados decide (apos o atraso)", async () => {
    const charge = await createQuota();
    const poll = async () => {
      const r = await mockContributions.getCharge("escola-exemplo", charge.token);
      if (!r.ok) throw new Error("poll falhou");
      return r.data.status;
    };
    expect(await poll()).toBe("PENDING");
    vi.advanceTimersByTime(PAY_DELAY_MS - 1);
    expect(await poll()).toBe("PENDING");
    vi.advanceTimersByTime(1);
    expect(await poll()).toBe("PAID");
  });

  it("recusa valor livre fora das regras da escola e aceita dentro", async () => {
    const low = await mockContributions.create({ ...base, amount: { kind: "CUSTOM", cents: 499 } });
    expect(low).toEqual({ ok: false, error: { kind: "http", status: 422 } });
    const high = await mockContributions.create({ ...base, amount: { kind: "CUSTOM", cents: 200_001 } });
    expect(high.ok).toBe(false);
    const ok = await mockContributions.create({ ...base, amount: { kind: "CUSTOM", cents: 1550 } });
    expect(ok.ok && ok.data.charge.amountCents).toBe(1550);
  });

  it("revalida os campos obrigatorios da escola", async () => {
    const result = await mockContributions.create({
      slug: "escola-exemplo",
      amount: { kind: "QUOTA", quotaId: "cota-anual" },
      identification: { guardianName: "Ana Lima" },
    });
    expect(result.ok).toBe(false);
  });

  it("nao confunde escolas: token de uma escola nao abre em outra", async () => {
    const charge = await createQuota();
    const other = await mockContributions.getCharge("escola-horizonte", charge.token);
    expect(other).toEqual({ ok: false, error: { kind: "not-found", status: 404 } });
  });

  it("comprovante so existe para pagamento confirmado", async () => {
    const charge = await createQuota();
    const before = await mockContributions.getReceipt("escola-exemplo", charge.token);
    expect(before).toEqual({ ok: false, error: { kind: "http", status: 409 } });
    vi.advanceTimersByTime(PAY_DELAY_MS);
    const after = await mockContributions.getReceipt("escola-exemplo", charge.token);
    expect(after.ok && after.data.status).toBe("PAID");
    expect(after.ok && after.data.amountCents).toBe(20000);
  });

  it("token desconhecido e plausivel devolve comprovante de exemplo deterministico", async () => {
    const a = await mockContributions.getReceipt("escola-exemplo", "demo-comprovante-0001");
    const b = await mockContributions.getReceipt("escola-exemplo", "demo-comprovante-0001");
    expect(a).toEqual(b);
    expect(a.ok && a.data.status).toBe("PAID");
  });

  it("comprovante de exemplo traz todos os campos visiveis da escola, para qualquer token", async () => {
    for (let i = 0; i < 200; i += 1) {
      const result = await mockContributions.getReceipt("escola-exemplo", `demo-token-${i}-xyz`);
      if (!result.ok) throw new Error("falhou");
      expect(result.data.identification.guardianName).toBeTruthy();
      expect(result.data.identification.studentName).toBeTruthy();
      expect(result.data.identification.classroom).toBeTruthy();
    }
    const horizonte = await mockContributions.getReceipt("escola-horizonte", "demo-comprovante-0001");
    expect(horizonte.ok && horizonte.data.identification).not.toHaveProperty("classroom");
  });

  it("rejeita token mal formado e escola inexistente", async () => {
    expect((await mockContributions.getReceipt("escola-exemplo", "../etc/passwd")).ok).toBe(false);
    expect((await mockContributions.getReceipt("nao-existe", "demo-comprovante-0001")).ok).toBe(false);
  });
});
