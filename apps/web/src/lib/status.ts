/**
 * Mapeamento de status. O frontend NUNCA decide que algo foi pago: ele so traduz o status
 * que a camada de dados devolveu. Status desconhecido nao vira "pago" em hipotese alguma.
 */

export type PixChargeStatus = "PENDING" | "PAID" | "REVIEW_REQUIRED" | "EXPIRED" | "CANCELLED";

export type MovementKind = "CONTRIBUTION" | "EXPENSE" | "REIMBURSEMENT" | "REFUND";
export type MovementStatus = "PENDING" | "APPROVED" | "CONFIRMED" | "REJECTED" | "CANCELLED" | "EXPIRED";

export type StatusTone = "neutral" | "success" | "warning" | "danger" | "info";

export interface StatusView {
  label: string;
  tone: StatusTone;
  /** Frase para leitores de tela e mensagens (aria-live). */
  description: string;
  /** Status final: o polling para. */
  final: boolean;
}

const PIX_STATUSES: readonly PixChargeStatus[] = ["PENDING", "PAID", "REVIEW_REQUIRED", "EXPIRED", "CANCELLED"];

/** Valida um valor vindo da camada de dados. Desconhecido vira null (nunca PAID). */
export function parsePixStatus(raw: unknown): PixChargeStatus | null {
  return typeof raw === "string" && (PIX_STATUSES as readonly string[]).includes(raw)
    ? (raw as PixChargeStatus)
    : null;
}

export function pixStatusView(status: PixChargeStatus | null): StatusView {
  switch (status) {
    case "PAID":
      return { label: "Pago", tone: "success", description: "Pagamento confirmado.", final: true };
    case "EXPIRED":
      return {
        label: "Expirado",
        tone: "warning",
        description: "O Pix expirou e nenhum valor foi cobrado.",
        final: true,
      };
    case "CANCELLED":
      return {
        label: "Cancelado",
        tone: "danger",
        description: "O Pix foi cancelado e nenhum valor foi cobrado.",
        final: true,
      };
    case "REVIEW_REQUIRED":
      return {
        label: "Em análise",
        tone: "warning",
        description: "O pagamento está em análise pela escola. A contribuição ainda não foi confirmada.",
        final: false,
      };
    case "PENDING":
      return {
        label: "Aguardando pagamento",
        tone: "info",
        description: "Aguardando a confirmação do pagamento.",
        final: false,
      };
    default:
      return {
        label: "Status indisponível",
        tone: "neutral",
        description: "Não foi possível saber o status deste pagamento.",
        final: false,
      };
  }
}

// ---------------------------------------------------------------- contribuicao (portal publico)

export type ContributionStatus = "PENDING_PAYMENT" | "PAID" | "REVIEW_REQUIRED" | "EXPIRED" | "CANCELLED";

const CONTRIBUTION_STATUSES: readonly ContributionStatus[] = [
  "PENDING_PAYMENT",
  "PAID",
  "REVIEW_REQUIRED",
  "EXPIRED",
  "CANCELLED",
];

/** Status da contribuicao vindo da API. Desconhecido vira null (nunca PAID). */
export function parseContributionStatus(raw: unknown): ContributionStatus | null {
  return typeof raw === "string" && (CONTRIBUTION_STATUSES as readonly string[]).includes(raw)
    ? (raw as ContributionStatus)
    : null;
}

/**
 * O que a tela da familia mostra. REGRA: "paid" so quando a CONTRIBUICAO veio PAID da API: uma
 * cobranca PAID sozinha (corrida entre as duas leituras) continua "waiting". Em duvida, nao e pago.
 */
export type PaymentPhase = "waiting" | "paid" | "review" | "expired" | "closed";

export function paymentPhase(state: {
  status: ContributionStatus;
  charge: { status: PixChargeStatus } | null;
}): PaymentPhase {
  if (state.status === "PAID") return "paid";
  if (state.status === "REVIEW_REQUIRED" || state.charge?.status === "REVIEW_REQUIRED") return "review";
  if (state.status === "CANCELLED" || state.status === "EXPIRED") return "closed";
  if (state.charge === null) return "closed";
  if (state.charge.status === "EXPIRED" || state.charge.status === "CANCELLED") return "expired";
  return "waiting";
}

export const KIND_LABELS: Record<MovementKind, string> = {
  CONTRIBUTION: "Contribuição",
  EXPENSE: "Despesa",
  REIMBURSEMENT: "Reembolso",
  REFUND: "Devolução",
};

type StatusTable = Partial<Record<MovementStatus, Omit<StatusView, "description"> & { description?: string }>>;

const MOVEMENT_STATUS: Record<MovementKind, StatusTable> = {
  CONTRIBUTION: {
    PENDING: { label: "Aguardando Pix", tone: "info", final: false },
    CONFIRMED: { label: "Pago", tone: "success", final: true },
    EXPIRED: { label: "Expirado", tone: "warning", final: true },
    CANCELLED: { label: "Cancelado", tone: "neutral", final: true },
  },
  EXPENSE: {
    PENDING: { label: "A pagar", tone: "warning", final: false },
    CONFIRMED: { label: "Paga", tone: "success", final: true },
    CANCELLED: { label: "Cancelada", tone: "neutral", final: true },
  },
  REIMBURSEMENT: {
    PENDING: { label: "Em análise", tone: "info", final: false },
    APPROVED: { label: "Aprovado", tone: "warning", final: false },
    CONFIRMED: { label: "Reembolsado", tone: "success", final: true },
    REJECTED: { label: "Recusado", tone: "danger", final: true },
  },
  REFUND: {
    PENDING: { label: "Em processamento", tone: "info", final: false },
    CONFIRMED: { label: "Devolvida", tone: "success", final: true },
    REJECTED: { label: "Não concluída", tone: "danger", final: true },
  },
};

export function movementStatusView(kind: MovementKind, status: MovementStatus): StatusView {
  const entry = MOVEMENT_STATUS[kind][status];
  if (!entry) {
    return { label: "Status indisponível", tone: "neutral", description: "Status não reconhecido.", final: false };
  }
  return {
    label: entry.label,
    tone: entry.tone,
    description: `${KIND_LABELS[kind]}: ${entry.label.toLowerCase()}.`,
    final: entry.final,
  };
}
