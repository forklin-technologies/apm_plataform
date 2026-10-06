import type { MovementKind, StatusTone } from "./status";

/**
 * Textos em portugues para os codigos do extrato. A API manda codigos (PAID, REIMBURSED, PAYABLE...);
 * nenhum texto do servidor e mostrado. Codigo desconhecido cai num rotulo neutro, nunca em "pago".
 */
export interface Label {
  label: string;
  tone: StatusTone;
}

export function entryStatus(kind: MovementKind, status: string, statusLabel: string): Label {
  if (statusLabel === "REIMBURSED") return { label: "Reembolsado", tone: "success" };
  if (status === "PAID" || status === "SETTLED") {
    switch (kind) {
      case "CONTRIBUTION":
        return { label: "Pago", tone: "success" };
      case "EXPENSE":
        return { label: "Paga", tone: "success" };
      case "REIMBURSEMENT":
        return { label: "Reembolsado", tone: "success" };
      case "REFUND":
        return { label: "Devolvida", tone: "success" };
    }
  }
  return { label: "Lançado no caixa", tone: "neutral" };
}

const PENDING_STATUS: Record<string, string> = {
  PENDING: "Pendente",
  PENDING_PAYMENT: "Aguardando Pix",
  SUBMITTED: "Enviada",
  CORRECTION_REQUESTED: "Correção pedida",
  APPROVED: "Aprovada",
  REVIEW_REQUIRED: "Em análise",
  REQUESTED: "Solicitada",
};

export function pendingStatus(status: string): string {
  return PENDING_STATUS[status] ?? "Pendente";
}

/** Secoes de statement_pending, na ordem em que a tela as mostra. */
export const PENDING_SECTIONS: Array<{ section: string; title: string; note: string }> = [
  { section: "PAYABLE", title: "A pagar", note: "Despesas aprovadas e reembolsos que ainda saem do caixa." },
  { section: "AWAITING_APPROVAL", title: "Aguardando aprovação", note: "Despesas enviadas que a tesouraria ainda não decidiu." },
  { section: "AWAITING_CORRECTION", title: "Aguardando correção", note: "Despesas devolvidas para quem enviou corrigir." },
  { section: "REVIEW", title: "Pagamento em análise", note: "Pix recebido que a escola precisa conferir. Ainda não é contribuição confirmada." },
  { section: "RECEIVABLE", title: "A receber", note: "Pix gerado e ainda não pago, e devoluções a receber." },
];

export function pendingSectionTitle(section: string): string {
  return PENDING_SECTIONS.find((s) => s.section === section)?.title ?? "Outras pendências";
}

const CATEGORY_LABELS: Record<string, string> = {
  parent_contribution: "Contribuição de pais",
  donation: "Doação",
  other_income: "Outra entrada",
  apm_revenue: "Receita da APM",
  teacher_reimbursement: "Reembolso de professor",
};

/** Categoria (codigo) em portugues; categoria desconhecida: o proprio codigo legivel, sem prosa do servidor. */
export function categoryLabel(key: string): string {
  return CATEGORY_LABELS[key] ?? key.replace(/_/g, " ");
}

const MONTH_FORMAT = new Intl.DateTimeFormat("pt-BR", { timeZone: "UTC", month: "long", year: "numeric" });

/** "2026-10" -> "outubro de 2026". */
export function periodLabel(period: string): string {
  const [year, month] = period.split("-").map(Number);
  if (!year || !month) return period;
  return MONTH_FORMAT.format(new Date(Date.UTC(year, month - 1, 1)));
}

/** Os `count` meses terminando em `latest` (YYYY-MM), do mais recente para o mais antigo. */
export function recentPeriods(latest: string, count = 12): string[] {
  const [year, month] = latest.split("-").map(Number);
  if (!year || !month) return [latest];
  const out: string[] = [];
  for (let i = 0; i < count; i += 1) {
    const index = year * 12 + (month - 1) - i;
    out.push(`${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`);
  }
  return out;
}

/** "2026-10-06" (data local da escola) -> "06 out". Sem Date: nao ha fuso para errar. */
export function formatLocalDateShort(localDate: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(localDate);
  if (!match) return localDate;
  const months = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
  return `${match[3]} ${months[Number(match[2]) - 1] ?? match[2]}`;
}

/** Mes corrente no relogio do navegador (YYYY-MM). A API usa o fuso da escola quando `period` falta. */
export function currentLocalPeriod(now: Date = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}
