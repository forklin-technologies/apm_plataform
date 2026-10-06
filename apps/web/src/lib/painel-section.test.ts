import { describe, expect, it } from "vitest";
import { parsePeriod, parseSection, sectionHref } from "./painel-section";

describe("painel sections", () => {
  it("converte o parametro da URL em tipo do dominio", () => {
    expect(parseSection("contribuicoes")).toBe("CONTRIBUTION");
    expect(parseSection("despesas")).toBe("EXPENSE");
    expect(parseSection("reembolsos")).toBe("REIMBURSEMENT");
    expect(parseSection("devolucoes")).toBe("REFUND");
  });

  it("cai no resumo para valores desconhecidos ou maliciosos", () => {
    for (const bad of [undefined, "", "x", "__proto__", "constructor", ["despesas", "x"][1], "<script>"]) {
      expect(parseSection(bad)).toBe("resumo");
    }
    expect(parseSection(["despesas", "x"])).toBe("EXPENSE");
  });

  it("monta links de ida e volta", () => {
    expect(sectionHref("resumo")).toBe("/painel");
    expect(parseSection(sectionHref("REFUND").split("=")[1])).toBe("REFUND");
  });

  it("o mes vai na URL e so aceita YYYY-MM valido", () => {
    expect(sectionHref("resumo", "2026-09")).toBe("/painel?mes=2026-09");
    expect(sectionHref("EXPENSE", "2026-09")).toBe("/painel?tipo=despesas&mes=2026-09");
    expect(sectionHref("EXPENSE", "2026-13")).toBe("/painel?tipo=despesas");
    expect(parsePeriod("2026-10")).toBe("2026-10");
    expect(parsePeriod(["2026-01", "x"])).toBe("2026-01");
    for (const bad of [undefined, "", "2026-13", "2026-00", "26-10", "2026-1", "2026-10-01", "../x", "<script>"]) {
      expect(parsePeriod(bad)).toBeUndefined();
    }
  });
});
