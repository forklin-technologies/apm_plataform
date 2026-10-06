import { describe, expect, it } from "vitest";
import type { ApiError } from "@/lib/api/types";
import { amountSummary, describeClosingError, describeExpenseError, expenseStatusView, formatFileSize } from "./expense-labels";

const problem = (code: string, status: number, extra: Partial<ApiError> = {}): ApiError => ({ kind: "problem", status, code, ...extra });

describe("expenseStatusView", () => {
  it("traduz cada estado; aprovada de colaborador e 'reembolso pendente'", () => {
    expect(expenseStatusView("DRAFT", "APM").label).toBe("Rascunho");
    expect(expenseStatusView("SUBMITTED", "APM").label).toBe("Enviada");
    expect(expenseStatusView("CORRECTION_REQUESTED", "COLLABORATOR").label).toBe("Correção pedida");
    expect(expenseStatusView("APPROVED", "COLLABORATOR").label).toBe("Reembolso pendente");
    expect(expenseStatusView("APPROVED", "APM").label).toBe("Aprovada, a pagar");
    expect(expenseStatusView("REJECTED", "APM")).toMatchObject({ label: "Recusada", tone: "danger" });
    expect(expenseStatusView("PAID", "COLLABORATOR")).toMatchObject({ label: "Paga", tone: "success" });
    expect(expenseStatusView("CANCELLED", "APM").label).toBe("Cancelada");
  });
});

describe("describeExpenseError", () => {
  it("regras de quem pode: o autor nunca decide, reembolsa nem paga a propria despesa", () => {
    expect(describeExpenseError(problem("self_approval_forbidden", 403)).general).toMatch(/própria despesa/);
    expect(describeExpenseError(problem("self_reimbursement_forbidden", 403)).general).toMatch(/seu próprio reembolso/);
    expect(describeExpenseError(problem("self_payment_forbidden", 403)).general).toMatch(/pagamento da sua própria despesa/);
    expect(describeExpenseError(problem("author_only", 403)).general).toMatch(/Só quem enviou/);
    expect(describeExpenseError(problem("permission_denied", 403)).general).toMatch(/não tem permissão/);
  });

  it("409 (estado, anexos, pagamento, corrida) em portugues", () => {
    expect(describeExpenseError(problem("invalid_state", 409)).general).toMatch(/mudou de situação/);
    expect(describeExpenseError(problem("attachments_closed", 409)).general).toMatch(/rascunho/);
    expect(describeExpenseError(problem("attachment_limit", 409)).general).toMatch(/10 anexos/);
    expect(describeExpenseError(problem("attachment_duplicate", 409)).general).toMatch(/já foi anexado/);
    expect(describeExpenseError(problem("payment_by_reimbursement", 409)).general).toMatch(/registre o reembolso/);
    expect(describeExpenseError(problem("reimbursement_not_applicable", 409)).general).toMatch(/registre o pagamento/);
    for (const code of ["conflict", "state_conflict", "retry"]) expect(describeExpenseError(problem(code, 409)).general).toMatch(/Atualize/);
    expect(describeExpenseError(problem("codigo_novo", 409)).general).toMatch(/Atualize/);
  });

  it("upload: tamanho, tipo e 411/413 sem codigo", () => {
    expect(describeExpenseError(problem("attachment_type_not_allowed", 415)).general).toMatch(/PNG, JPEG, WebP ou PDF/);
    expect(describeExpenseError(problem("payload_too_large", 413)).general).toMatch(/10 MB/);
    expect(describeExpenseError({ kind: "http", status: 413 }).general).toMatch(/10 MB/);
  });

  it("422: erro por campo; envio incompleto lista o que falta; aprovacao parcial", () => {
    const fields = describeExpenseError(problem("validation_error", 422, { fields: [{ field: "category_id", code: "not_available" }, { field: "approved_amount_cents", code: "above_requested" }, { field: "outro", code: "x" }] }));
    expect(fields.fields).toEqual({
      category_id: "Escolha uma categoria da lista.",
      approved_amount_cents: "O valor aprovado não pode ser maior que o pedido.",
      outro: "Confira este campo.",
    });
    const incomplete = describeExpenseError(
      problem("expense_incomplete", 422, { fields: [{ field: "purchase_reason", code: "required" }, { field: "payment_method", code: "required" }, { field: "attachments", code: "required" }] }),
    );
    expect(incomplete.general).toBe("Para enviar a despesa, informe o motivo da compra, informe a forma de pagamento, anexe a nota ou o recibo.");
    expect(describeExpenseError(problem("partial_approval_not_allowed", 422)).general).toMatch(/colaborador pagou/);
  });

  it("rede, 429, sessao e codigo desconhecido; nunca texto do servidor", () => {
    expect(describeExpenseError({ kind: "network" }).general).toMatch(/Nada foi alterado/);
    expect(describeExpenseError(problem("rate_limited", 429, { retryAfterSeconds: 90 })).general).toContain("2 minutos");
    expect(describeExpenseError(problem("session_revoked", 401)).general).toMatch(/sessão terminou/);
    expect(describeExpenseError(problem("internal_error", 500)).general).toMatch(/do nosso lado/);
    const hostile = { kind: "problem", status: 500, code: "internal_error", title: "Internal Server Error", detail: "x" } as ApiError;
    expect(JSON.stringify(describeExpenseError(hostile))).not.toMatch(/Internal Server Error/);
  });
});

describe("describeClosingError", () => {
  it("meses em ordem: mostra o proximo mes vindo do detalhe; mes que nao acabou; reaberto sem PDF", () => {
    expect(describeClosingError(problem("closing_out_of_sequence", 409, { detailPeriod: "2026-02" }))).toBe("Os meses são fechados em ordem. O próximo mês a fechar é fevereiro de 2026.");
    expect(describeClosingError(problem("closing_out_of_sequence", 409))).toMatch(/Feche primeiro o mês anterior/);
    expect(describeClosingError(problem("period_not_ended", 409))).toMatch(/ainda não terminou/);
    expect(describeClosingError(problem("closing_reopened", 409))).toMatch(/reaberto e não tem PDF/);
    expect(describeClosingError(problem("closing_mismatch", 409))).toMatch(/não bate/);
    expect(describeClosingError(problem("closing_not_latest", 409))).toMatch(/último fechamento/);
    expect(describeClosingError(problem("period_already_closed", 409))).toMatch(/já está fechado/);
  });

  it("permissao, validacao, rede e generico", () => {
    expect(describeClosingError(problem("permission_denied", 403))).toMatch(/não tem permissão/);
    expect(describeClosingError(problem("validation_error", 422))).toMatch(/10 a 500/);
    expect(describeClosingError({ kind: "timeout" })).toMatch(/conexão/);
    expect(describeClosingError(problem("internal_error", 500))).toMatch(/do nosso lado/);
  });
});

describe("helpers", () => {
  it("tamanho de arquivo e valor aprovado diferente do pedido", () => {
    expect(formatFileSize(500)).toBe("500 B");
    expect(formatFileSize(2048)).toBe("2 KB");
    expect(formatFileSize(2.5 * 1024 * 1024)).toBe("2,5 MB");
    expect(amountSummary(20000, 17500)).toMatch(/R\$\s175,00 \(pedido: R\$\s200,00\)/);
    expect(amountSummary(20000, null)).toMatch(/R\$\s200,00/);
    expect(amountSummary(20000, 20000)).toMatch(/R\$\s200,00$/);
  });
});
