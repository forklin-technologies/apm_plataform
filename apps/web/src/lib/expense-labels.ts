import { formatWait } from "@/lib/auth-messages";
import { formatBRL } from "@/lib/money";
import { periodLabel } from "@/lib/statement-labels";
import type { ApiError, AttachmentKind, ExpenseStatus, PaidBy, PaymentMethod } from "@/lib/api/types";
import type { StatusTone } from "@/lib/status";

/**
 * Textos em portugues para despesas e fechamento. A API manda codigos (DRAFT, PAID, self_approval_forbidden...);
 * nenhum texto do servidor e mostrado. Codigo desconhecido cai num texto generico, nunca num "deu certo".
 */
export function expenseStatusView(status: ExpenseStatus, paidBy: PaidBy): { label: string; tone: StatusTone } {
  switch (status) {
    case "DRAFT":
      return { label: "Rascunho", tone: "neutral" };
    case "SUBMITTED":
      return { label: "Enviada", tone: "info" };
    case "CORRECTION_REQUESTED":
      return { label: "Correção pedida", tone: "warning" };
    case "APPROVED":
      return paidBy === "COLLABORATOR" ? { label: "Reembolso pendente", tone: "warning" } : { label: "Aprovada, a pagar", tone: "warning" };
    case "REJECTED":
      return { label: "Recusada", tone: "danger" };
    case "PAID":
      return { label: "Paga", tone: "success" };
    case "CANCELLED":
      return { label: "Cancelada", tone: "neutral" };
  }
}

export const PAID_BY_LABELS: Record<PaidBy, string> = {
  APM: "Paga pela APM",
  COLLABORATOR: "Paga do bolso de quem enviou (reembolso)",
};

export const PAYMENT_METHOD_LABELS: Record<PaymentMethod, string> = {
  PIX: "Pix",
  CARD: "Cartão",
  CASH: "Dinheiro",
  OTHER: "Outra forma",
};

export const ATTACHMENT_KIND_LABELS: Record<AttachmentKind, string> = {
  INVOICE: "Nota ou recibo",
  PAYMENT_PROOF: "Comprovante de pagamento",
  OTHER: "Outro arquivo",
};

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} MB`;
}

export interface ProblemText {
  general: string | null;
  /** Por nome de campo da API (category_id, amount_cents, reason...). */
  fields: Record<string, string>;
}

const NETWORK = "Não foi possível falar com o servidor. Confira a conexão e tente de novo. Nada foi alterado.";
const GENERIC = "Algo deu errado do nosso lado. Tente de novo em instantes.";
const STALE = "Outra pessoa acabou de mexer nesta despesa. Atualize a lista e tente de novo.";

const MISSING_FOR_SUBMIT: Record<string, string> = {
  attachments: "anexe a nota ou o recibo",
  purchase_reason: "informe o motivo da compra",
  payment_method: "informe a forma de pagamento",
};

const FIELD_TEXT: Record<string, Record<string, string>> = {
  category_id: { not_available: "Escolha uma categoria da lista.", required: "Escolha uma categoria." },
  amount_cents: { required: "Informe o valor.", value_out_of_range: "O valor está fora do que é aceito." },
  approved_amount_cents: {
    above_requested: "O valor aprovado não pode ser maior que o pedido.",
    value_out_of_range: "O valor aprovado está fora do que é aceito.",
  },
  occurred_at: { required: "Informe a data da despesa.", invalid: "Confira a data." },
  description: { required: "Descreva a despesa.", invalid: "Confira a descrição." },
  purchase_reason: { required: "Informe o motivo da compra.", invalid: "Confira o motivo da compra." },
  payment_method: { required: "Informe a forma de pagamento." },
  reason: { required: "Escreva o motivo.", invalid: "O motivo precisa ter de 3 a 500 caracteres." },
  payment_reference: { required: "Informe a referência do pagamento.", invalid: "Confira a referência do pagamento." },
};

/** Erro de uma acao sobre despesa (criar, editar, enviar, aprovar, recusar, pedir correcao, reembolsar, pagar, cancelar, anexar). */
export function describeExpenseError(error: ApiError): ProblemText {
  const none: ProblemText = { general: null, fields: {} };
  if (error.kind === "network" || error.kind === "timeout") return { ...none, general: NETWORK };
  if (error.status === 411 || error.status === 413) return { ...none, general: "O arquivo é grande demais. O limite é de 10 MB." };
  if (error.kind !== "problem") return { ...none, general: GENERIC };

  switch (error.code) {
    case "unauthenticated":
    case "session_revoked":
      return { ...none, general: "Sua sessão terminou. Entre de novo." };
    case "permission_denied":
      return { ...none, general: "Você não tem permissão para fazer isso." };
    case "author_only":
      return { ...none, general: "Só quem enviou a despesa pode alterá-la." };
    case "self_approval_forbidden":
      return { ...none, general: "Você não pode aprovar, recusar nem pedir correção da sua própria despesa. Outra pessoa da gestão precisa decidir." };
    case "self_reimbursement_forbidden":
      return { ...none, general: "Você não pode registrar o seu próprio reembolso. Outra pessoa da gestão precisa registrar." };
    case "self_payment_forbidden":
      return { ...none, general: "Você não pode registrar o pagamento da sua própria despesa. Outra pessoa da gestão precisa registrar." };
    case "not_found":
      return { ...none, general: "Não encontramos esta despesa. Atualize a lista." };
    case "invalid_state":
      return { ...none, general: "Esta despesa mudou de situação e essa ação não vale mais. Atualize a lista." };
    case "attachments_closed":
      return { ...none, general: "A despesa já foi enviada: só dá para anexar arquivos em rascunho ou quando a gestão pede correção." };
    case "attachment_limit":
      return { ...none, general: "Esta despesa já tem o máximo de 10 anexos." };
    case "attachment_duplicate":
      return { ...none, general: "Este arquivo já foi anexado a esta despesa." };
    case "attachment_type_not_allowed":
      return { ...none, general: "Tipo de arquivo não aceito. Envie PNG, JPEG, WebP ou PDF." };
    case "payload_too_large":
    case "length_required":
      return { ...none, general: "O arquivo é grande demais. O limite é de 10 MB." };
    case "attachment_unavailable":
      return { ...none, general: "O arquivo não está disponível agora. Avise a tesouraria." };
    case "partial_approval_not_allowed":
      return { ...none, general: "Só dá para aprovar um valor menor quando um colaborador pagou do próprio bolso. Aprove pelo valor pedido." };
    case "reimbursement_not_applicable":
      return { ...none, general: "Esta despesa foi paga pela APM: registre o pagamento, não o reembolso." };
    case "payment_by_reimbursement":
      return { ...none, general: "Esta despesa foi paga por um colaborador: registre o reembolso, não o pagamento." };
    case "reimbursement_exists":
      return { ...none, general: "O reembolso desta despesa já foi registrado. Atualize a lista." };
    case "reimbursement_category_missing":
      return { ...none, general: "A escola ainda não tem a categoria de reembolso configurada. Avise a administração." };
    case "conflict":
    case "state_conflict":
    case "retry":
      return { ...none, general: STALE };
    case "rate_limited":
      return { ...none, general: `Muitas tentativas seguidas. Aguarde ${formatWait(error.retryAfterSeconds)} e tente de novo.` };
    case "csrf_failed":
    case "origin_not_allowed":
      return { ...none, general: "Não foi possível confirmar que o pedido saiu desta página. Recarregue a página e tente de novo." };
    case "expense_incomplete": {
      const missing = (error.fields ?? []).map((f) => MISSING_FOR_SUBMIT[f.field]).filter(Boolean);
      return {
        ...none,
        general: missing.length > 0 ? `Para enviar a despesa, ${missing.join(", ")}.` : "Faltam dados para enviar a despesa. Confira o formulário.",
      };
    }
    case "validation_error":
    case "rule_violation":
    case "reference_invalid":
    case "value_out_of_range": {
      const out: ProblemText = { general: null, fields: {} };
      for (const item of error.fields ?? []) {
        out.fields[item.field] = FIELD_TEXT[item.field]?.[item.code] ?? "Confira este campo.";
      }
      if (Object.keys(out.fields).length === 0) out.general = "Confira os dados informados e tente de novo.";
      return out;
    }
    default:
      return { ...none, general: error.status === 409 ? STALE : GENERIC };
  }
}

/** Erro do fechamento mensal (criar, verificar, reabrir, baixar o PDF). */
export function describeClosingError(error: ApiError): string {
  if (error.kind === "network" || error.kind === "timeout") return NETWORK;
  if (error.kind !== "problem") return GENERIC;
  switch (error.code) {
    case "unauthenticated":
    case "session_revoked":
      return "Sua sessão terminou. Entre de novo.";
    case "permission_denied":
      return "Você não tem permissão para fazer isso.";
    case "not_found":
      return "Não encontramos este fechamento. Atualize a página.";
    case "period_not_ended":
      return "Esse mês ainda não terminou. Só dá para fechar meses que já acabaram.";
    case "closing_out_of_sequence":
      return error.detailPeriod
        ? `Os meses são fechados em ordem. O próximo mês a fechar é ${periodLabel(error.detailPeriod)}.`
        : "Os meses são fechados em ordem. Feche primeiro o mês anterior.";
    case "period_already_closed":
      return "Esse mês já está fechado. Para fechar de novo, reabra o fechamento antes.";
    case "closing_not_latest":
      return "Só dá para reabrir o último fechamento.";
    case "closing_already_reopened":
    case "closing_reopened":
      return "Este fechamento foi reaberto e não tem PDF nem verificação. Feche o mês de novo para gerar um novo.";
    case "closing_mismatch":
    case "report_mismatch":
      return "O relatório não bate com os números do fechamento e por isso não foi gerado. Avise a administração.";
    case "rate_limited":
      return `Muitas tentativas seguidas. Aguarde ${formatWait(error.retryAfterSeconds)} e tente de novo.`;
    case "csrf_failed":
    case "origin_not_allowed":
      return "Não foi possível confirmar que o pedido saiu desta página. Recarregue a página e tente de novo.";
    case "validation_error":
      return "Confira os dados informados (mês no formato certo, motivo com 10 a 500 caracteres) e tente de novo.";
    default:
      return GENERIC;
  }
}

/** "R$ 175,00" para o valor aprovado ou pedido: o aprovado, quando existir e for diferente, vem entre parenteses. */
export function amountSummary(amountCents: number, approvedCents: number | null): string {
  if (approvedCents !== null && approvedCents !== amountCents) return `${formatBRL(approvedCents)} (pedido: ${formatBRL(amountCents)})`;
  return formatBRL(amountCents);
}
