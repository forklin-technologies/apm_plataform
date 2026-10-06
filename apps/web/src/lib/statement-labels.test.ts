import { describe, expect, it } from "vitest";
import {
  categoryLabel,
  currentLocalPeriod,
  entryStatus,
  formatLocalDateShort,
  pendingSectionTitle,
  pendingStatus,
  periodLabel,
  recentPeriods,
} from "./statement-labels";

describe("statement-labels", () => {
  it("status de lancamento: por tipo; desconhecido nunca vira 'Pago'", () => {
    expect(entryStatus("CONTRIBUTION", "PAID", "PAID")).toMatchObject({ label: "Pago", tone: "success" });
    expect(entryStatus("EXPENSE", "PAID", "PAID").label).toBe("Paga");
    expect(entryStatus("REIMBURSEMENT", "PAID", "REIMBURSED").label).toBe("Reembolsado");
    expect(entryStatus("REFUND", "PAID", "PAID").label).toBe("Devolvida");
    const unknown = entryStatus("CONTRIBUTION", "WEIRD", "WEIRD");
    expect(unknown.label).toBe("Lançado no caixa");
    expect(unknown.tone).toBe("neutral");
  });

  it("pendencias: secoes e status em portugues, com padrao neutro", () => {
    expect(pendingSectionTitle("REVIEW")).toBe("Pagamento em análise");
    expect(pendingSectionTitle("NOVA")).toBe("Outras pendências");
    expect(pendingStatus("PENDING_PAYMENT")).toBe("Aguardando Pix");
    expect(pendingStatus("CORRECTION_REQUESTED")).toBe("Correção pedida");
    expect(pendingStatus("XYZ")).toBe("Pendente");
  });

  it("categorias conhecidas e codigo legivel para as outras", () => {
    expect(categoryLabel("parent_contribution")).toBe("Contribuição de pais");
    expect(categoryLabel("teacher_reimbursement")).toBe("Reembolso de professor");
    expect(categoryLabel("bank_fee")).toBe("bank fee");
  });

  it("mes: rotulo, lista dos ultimos meses (atravessa o ano) e mes local", () => {
    expect(periodLabel("2026-10")).toBe("outubro de 2026");
    expect(periodLabel("2026-01")).toBe("janeiro de 2026");
    expect(recentPeriods("2026-02", 4)).toEqual(["2026-02", "2026-01", "2025-12", "2025-11"]);
    expect(recentPeriods("2026-10", 12)).toHaveLength(12);
    expect(currentLocalPeriod(new Date(2026, 9, 6))).toBe("2026-10");
    expect(currentLocalPeriod(new Date(2026, 0, 31))).toBe("2026-01");
  });

  it("data local da escola sem fuso para errar", () => {
    expect(formatLocalDateShort("2026-10-06")).toBe("06 out");
    expect(formatLocalDateShort("2026-01-31")).toBe("31 jan");
    expect(formatLocalDateShort("qualquer")).toBe("qualquer");
  });
});
