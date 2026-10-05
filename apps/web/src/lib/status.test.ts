import { describe, expect, it } from "vitest";
import {
  KIND_LABELS,
  movementStatusView,
  parsePixStatus,
  pixStatusView,
  type MovementKind,
  type MovementStatus,
} from "./status";

describe("parsePixStatus", () => {
  it("aceita so os status do contrato", () => {
    for (const ok of ["PENDING", "PAID", "EXPIRED", "CANCELLED"]) {
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
