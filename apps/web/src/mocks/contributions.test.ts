import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEMO_RECEIPT_TOKEN } from "@/lib/api/token";
import { CHARGE_TTL_MS, ORDER_TTL_MS, PAY_DELAY_MS, mockContributions, resetMockOrders, sampleReceipt } from "./contributions";
import { SCHOOLS } from "./fixtures";

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

  it("N1: SO o token de exemplo tem comprovante fixo; qualquer outro token desconhecido e 'nao encontrado'", async () => {
    const a = await mockContributions.getReceipt("escola-exemplo", DEMO_RECEIPT_TOKEN);
    const b = await mockContributions.getReceipt("escola-exemplo", DEMO_RECEIPT_TOKEN);
    expect(a).toEqual(b);
    expect(a.ok && a.data.status).toBe("PAID");
    for (const unknown of ["AbCdEfGhIjKlMnOpQrStUv", "a".repeat(22), "demo-comprovante-0002", "x".repeat(64)]) {
      expect(await mockContributions.getReceipt("escola-exemplo", unknown)).toEqual({
        ok: false,
        error: { kind: "not-found", status: 404 },
      });
    }
  });

  it("N1: nao diferencia 'nunca existiu' de 'ainda nao existe': mesma resposta e mesma forma", async () => {
    const charge = await createQuota(); // existe, mas em OUTRA escola
    const wrongSchool = await mockContributions.getReceipt("escola-horizonte", charge.token);
    const neverExisted = await mockContributions.getReceipt("escola-horizonte", "zzzzzzzzzzzzzzzzzzzzzz");
    expect(wrongSchool).toEqual(neverExisted);
  });

  it("o comprovante de exemplo traz todos os campos visiveis da escola (regressao do indice com sinal)", () => {
    const school = SCHOOLS.find((x) => x.slug === "escola-exemplo")!;
    for (let i = 0; i < 300; i += 1) {
      const receipt = sampleReceipt(school, `demo-token-${i}-xyz`);
      expect(receipt.identification.guardianName).toBeTruthy();
      expect(receipt.identification.studentName).toBeTruthy();
      expect(receipt.identification.classroom).toBeTruthy();
    }
    const horizonte = SCHOOLS.find((x) => x.slug === "escola-horizonte")!;
    expect(sampleReceipt(horizonte, DEMO_RECEIPT_TOKEN).identification).not.toHaveProperty("classroom");
  });

  it("N8: clearPersonalData apaga nomes e turma do navegador, mas o comprovante segue abrindo sem eles", async () => {
    const charge = await createQuota();
    vi.advanceTimersByTime(PAY_DELAY_MS);
    const before = await mockContributions.getReceipt("escola-exemplo", charge.token);
    expect(before.ok && before.data.identification.guardianName).toBe("Ana Lima");
    expect(window.sessionStorage.getItem("apm-proto:orders")).toContain("Ana Lima");

    await mockContributions.clearPersonalData("escola-exemplo", charge.token);

    const raw = window.sessionStorage.getItem("apm-proto:orders") ?? "";
    expect(raw).not.toContain("Ana Lima");
    expect(raw).not.toContain("Davi Lima");
    const after = await mockContributions.getReceipt("escola-exemplo", charge.token);
    expect(after.ok && after.data.identification).toEqual({});
    expect(after.ok && after.data.amountCents).toBe(20000);
  });

  it("N8: o pedido some do navegador apos o TTL curto, mesmo sem abrir o comprovante", async () => {
    const charge = await createQuota();
    expect(window.sessionStorage.getItem("apm-proto:orders")).toContain("Ana Lima");
    vi.advanceTimersByTime(ORDER_TTL_MS - 1000);
    expect((await mockContributions.getCharge("escola-exemplo", charge.token)).ok).toBe(true);
    vi.advanceTimersByTime(1000);
    expect(await mockContributions.getCharge("escola-exemplo", charge.token)).toEqual({
      ok: false,
      error: { kind: "not-found", status: 404 },
    });
    expect(window.sessionStorage.getItem("apm-proto:orders") ?? "").not.toContain("Ana Lima");
  });

  it("N11: o token gerado tem 22 caracteres base64url e e aceito pelo formato", async () => {
    const charge = await createQuota();
    expect(charge.token).toMatch(/^[A-Za-z0-9_-]{22}$/);
  });

  it("rejeita token mal formado e escola inexistente", async () => {
    expect((await mockContributions.getReceipt("escola-exemplo", "../etc/passwd")).ok).toBe(false);
    expect((await mockContributions.getReceipt("escola-exemplo", "curto")).ok).toBe(false);
    expect((await mockContributions.getReceipt("nao-existe", DEMO_RECEIPT_TOKEN)).ok).toBe(false);
  });
});
