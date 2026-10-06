import { formatWait } from "@/lib/auth-messages";
import type { ApiError } from "@/lib/api/types";
import { FIELD_FROM_API, MISSING_MESSAGES, type IdentificationErrors } from "@/lib/validation";

/**
 * Textos em portugues para os erros do portal publico, escolhidos pelo `code` e pelo
 * `errors[].field`/`code` do problem+json. O title/detail do servidor NUNCA aparece.
 */
const NETWORK = "Não foi possível falar com o servidor. Confira a conexão e tente de novo. Nada foi cobrado.";
const GENERIC = "Não foi possível gerar o Pix agora. Tente de novo em instantes. Nada foi cobrado.";
const UNAVAILABLE = "O Pix ainda não está disponível para esta escola. Fale com a tesouraria da APM.";

export interface ContributionProblems {
  general: string | null;
  amount: string | null;
  fields: IdentificationErrors;
}

function rateLimited(error: ApiError): string {
  return `Muitas tentativas seguidas. Aguarde ${formatWait(error.retryAfterSeconds)} e tente de novo.`;
}

/** Erro do POST de criacao da contribuicao: geral, do valor e de cada campo. */
export function describeContributionError(error: ApiError): ContributionProblems {
  const none: ContributionProblems = { general: null, amount: null, fields: {} };
  if (error.kind === "network" || error.kind === "timeout") return { ...none, general: NETWORK };
  if (error.status === 501) return { ...none, general: UNAVAILABLE };
  if (error.kind !== "problem") return { ...none, general: GENERIC };

  switch (error.code) {
    case "rate_limited":
      return { ...none, general: rateLimited(error) };
    case "not_found":
      return { ...none, general: "Não encontramos esta escola. Confira o endereço que a escola enviou." };
    case "idempotency_key_reused":
      return { ...none, general: "Algo mudou entre uma tentativa e outra. Confira os dados e tente de novo." };
    case "validation_error": {
      const out: ContributionProblems = { general: null, amount: null, fields: {} };
      for (const item of error.fields ?? []) {
        if (item.field === "amount_cents") {
          out.amount =
            item.code === "not_suggested"
              ? "Esta escola aceita só os valores sugeridos."
              : item.code === "out_of_range"
                ? "O valor não está dentro do que a escola aceita."
                : "Confira o valor da contribuição.";
        } else if (item.field in FIELD_FROM_API) {
          const field = FIELD_FROM_API[item.field]!;
          out.fields[field] = item.code === "required" ? MISSING_MESSAGES[field] : "Confira este campo.";
        } else {
          out.general = "Não foi possível enviar o pedido. Recarregue a página e tente de novo.";
        }
      }
      if (!out.general && !out.amount && Object.keys(out.fields).length === 0) {
        out.general = "Algum dado não foi aceito pela escola. Volte e confira o valor e os campos.";
      }
      return out;
    }
    default:
      return { ...none, general: GENERIC };
  }
}

/** Erro de "gerar novo QR" (POST .../charges). */
export function describeRenewError(error: ApiError): string {
  if (error.kind === "network" || error.kind === "timeout") return NETWORK;
  if (error.status === 501) return UNAVAILABLE;
  if (error.kind === "problem") {
    if (error.code === "rate_limited") return rateLimited(error);
    if (error.code === "contribution_closed") return "Esta contribuição não está mais aguardando pagamento. Comece uma nova.";
  }
  return "Não foi possível gerar um novo QR agora. Tente de novo em instantes.";
}
