import { describe, expect, it } from "vitest";
import { parseSection, sectionHref } from "./painel-section";

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
});
