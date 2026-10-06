import { describe, expect, it } from "vitest";
import {
  KIND_LABELS,
  movementStatusView,
  parseContributionStatus,
  parsePixStatus,
  paymentPhase,
  pixStatusView,
  type MovementKind,
  type MovementStatus,
} from "./status";

describe("parsePixStatus", () => {
  it("aceita so os status do contrato", () => {
    for (const ok of ["PENDING", "PAID", "REVIEW_REQUIRED", "EXPIRED", "CANCELLED"]) {
      expect(parsePixStatus(ok)).toBe(ok);
    }
  });

  it("status desconhecido ou mal formado nunca vira PAID", () => {
    for (const bad of ["paid", "Pago", "SETTLED", "", null, undefined, 1, {}, ["PAID"]]) {
      expect(parsePixStatus(bad)).toBeNull();
    }
  });
});

describe("pixStatusView", () => {
  it("traduz cada status", () => {
    expect(pixStatusView("PENDING")).toMatchObject({ label: "Aguardando pagamento", final: false });
    expect(pixStatusView("PAID")).toMatchObject({ label: "Pago", tone: "success", final: true });
    expect(pixStatusView("EXPIRED")).toMatchObject({ label: "Expirado", final: true });
    expect(pixStatusView("CANCELLED")).toMatchObject({ label: "Cancelado", final: true });
  });

  it("status ausente nao e final nem de sucesso", () => {
    const view = pixStatusView(null);
    expect(view.final).toBe(false);
    expect(view.tone).toBe("neutral");
  });
});

describe("movementStatusView", () => {
  it("os quatro tipos do dominio tem rotulos distintos", () => {
    const labels = Object.values(KIND_LABELS);
    expect(new Set(labels).size).toBe(4);
    expect(labels).toEqual(["Contribuição", "Despesa", "Reembolso", "Devolução"]);
  });

  it("cada tipo usa o vocabulario proprio", () => {
    expect(movementStatusView("CONTRIBUTION", "CONFIRMED").label).toBe("Pago");
    expect(movementStatusView("EXPENSE", "CONFIRMED").label).toBe("Paga");
    expect(movementStatusView("REIMBURSEMENT", "CONFIRMED").label).toBe("Reembolsado");
    expect(movementStatusView("REFUND", "CONFIRMED").label).toBe("Devolvida");
    expect(movementStatusView("REIMBURSEMENT", "PENDING").label).toBe("Em análise");
  });

  it("combinacao inexistente cai em 'indisponivel', nunca em sucesso", () => {
    const kinds: MovementKind[] = ["CONTRIBUTION", "EXPENSE", "REIMBURSEMENT", "REFUND"];
    const statuses: MovementStatus[] = ["PENDING", "APPROVED", "CONFIRMED", "REJECTED", "CANCELLED", "EXPIRED"];
    const view = movementStatusView("EXPENSE", "APPROVED");
    expect(view.label).toBe("Status indisponível");
    expect(view.tone).toBe("neutral");
    for (const kind of kinds) {
      for (const status of statuses) {
        const v = movementStatusView(kind, status);
        if (v.label === "Status indisponível") expect(v.tone).not.toBe("success");
      }
    }
  });
});

describe("REVIEW_REQUIRED e paymentPhase (a contribuicao so e paga quando a API diz PAID)", () => {
  const charge = (status: "PENDING" | "PAID" | "REVIEW_REQUIRED" | "EXPIRED" | "CANCELLED") => ({ status });

  it("em analise nao e pago nem final", () => {
    expect(pixStatusView("REVIEW_REQUIRED")).toMatchObject({ label: "Em análise", final: false });
    expect(pixStatusView("REVIEW_REQUIRED").description).toMatch(/não foi confirmada/);
  });

  it("parseContributionStatus: so os do contrato; desconhecido vira null", () => {
    for (const ok of ["PENDING_PAYMENT", "PAID", "REVIEW_REQUIRED", "EXPIRED", "CANCELLED"]) expect(parseContributionStatus(ok)).toBe(ok);
    for (const bad of ["paid", "CONFIRMED", "", null, 1]) expect(parseContributionStatus(bad)).toBeNull();
  });

  it("fases", () => {
    expect(paymentPhase({ status: "PAID", charge: charge("PAID") })).toBe("paid");
    expect(paymentPhase({ status: "PENDING_PAYMENT", charge: charge("PENDING") })).toBe("waiting");
    expect(paymentPhase({ status: "PENDING_PAYMENT", charge: charge("EXPIRED") })).toBe("expired");
    expect(paymentPhase({ status: "PENDING_PAYMENT", charge: charge("CANCELLED") })).toBe("expired");
    expect(paymentPhase({ status: "REVIEW_REQUIRED", charge: charge("REVIEW_REQUIRED") })).toBe("review");
    expect(paymentPhase({ status: "PENDING_PAYMENT", charge: charge("REVIEW_REQUIRED") })).toBe("review");
    expect(paymentPhase({ status: "CANCELLED", charge: charge("CANCELLED") })).toBe("closed");
    expect(paymentPhase({ status: "EXPIRED", charge: null })).toBe("closed");
    expect(paymentPhase({ status: "PENDING_PAYMENT", charge: null })).toBe("closed");
  });

  it("cobranca PAID sozinha (contribuicao ainda pendente) NAO e paga", () => {
    expect(paymentPhase({ status: "PENDING_PAYMENT", charge: charge("PAID") })).toBe("waiting");
  });
});
