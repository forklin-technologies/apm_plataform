import type { ApiError } from "@/lib/api/types";

/**
 * Textos em portugues para os erros da API, escolhidos pelo `code` estavel do problem+json.
 * NUNCA se mostra o title/detail do servidor (ingles, pode mudar, nao e para o usuario).
 * Os textos de login nao distinguem e-mail inexistente, conta inativa e senha errada.
 */
export type AuthScene = "login" | "logout" | "context" | "invitation" | "password";

const NETWORK = "Não foi possível falar com o servidor. Confira a conexão e tente de novo.";
const GENERIC = "Algo deu errado do nosso lado. Tente de novo em instantes.";
const ORIGIN = "Não foi possível confirmar que o pedido saiu desta página. Recarregue a página e tente de novo.";

export function formatWait(seconds: number | undefined): string {
  if (seconds === undefined || seconds < 1) return "alguns minutos";
  if (seconds < 60) return seconds === 1 ? "1 segundo" : `${seconds} segundos`;
  const minutes = Math.ceil(seconds / 60);
  return minutes === 1 ? "1 minuto" : `${minutes} minutos`;
}

/** Erro que significa "nao ha sessao valida": a tela deve voltar para /login. */
export function isSessionLost(error: ApiError): boolean {
  return error.kind === "problem" && (error.code === "unauthenticated" || error.code === "session_revoked");
}

export function describeAuthError(error: ApiError, scene: AuthScene): string {
  if (error.kind === "network" || error.kind === "timeout") return NETWORK;
  if (error.kind !== "problem") return GENERIC;

  switch (error.code) {
    case "invalid_credentials":
      return "E-mail ou senha incorretos. Confira os dados e tente de novo.";
    case "rate_limited":
      return `Muitas tentativas seguidas. Aguarde ${formatWait(error.retryAfterSeconds)} e tente de novo.`;
    case "origin_not_allowed":
    case "csrf_failed":
      return ORIGIN;
    case "unauthenticated":
    case "session_revoked":
      return "Sua sessão terminou. Entre de novo.";
    case "context_not_allowed":
      return "Você não tem acesso a esse vínculo. Atualize a página e escolha outro.";
    case "context_required":
      return "Escolha onde você vai atuar antes de continuar.";
    case "permission_denied":
      return "Você não tem permissão para fazer isso.";
    case "invitation_invalid":
      return "Este convite não é válido. Ele pode ter expirado, já ter sido usado ou ter sido cancelado. Peça um novo convite a quem convidou você.";
    case "account_exists_login_required":
      return "Já existe uma conta com o e-mail deste convite. Entre com ela (saia da conta atual, se estiver conectado com outra) e abra este link de novo.";
    case "current_password_incorrect":
      return "A senha atual está incorreta.";
    case "weak_password":
      return "A senha não atende aos requisitos. Use de 12 a 128 caracteres.";
    case "validation_error":
      if (scene === "login") return "Informe o e-mail e a senha.";
      return "Confira os dados informados e tente de novo.";
    default:
      return GENERIC;
  }
}

const FIELD_CODES: Record<string, string> = {
  missing: "Preencha este campo.",
  blank: "Preencha este campo.",
  too_short: "Use pelo menos 12 caracteres.",
  too_long: "Este valor é longo demais.",
  same_as_current: "A nova senha precisa ser diferente da atual.",
};

/** Mensagem de um erro de campo (422) a partir do `code` fixo; desconhecido vira um texto neutro. */
export function describeFieldError(code: string): string {
  return FIELD_CODES[code] ?? "Confira este campo.";
}
