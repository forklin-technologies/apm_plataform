/**
 * Destino depois do login (?next=). Lista FECHADA de formas aceitas, sem interpretar URL:
 * qualquer outra coisa (inclusive `//host`, `https://...`, `\\`) cai no painel. Evita redirecionamento aberto.
 */
const DEFAULT_NEXT = "/painel";
const ALLOWED = [/^\/painel$/, /^\/painel\?tipo=[a-z]{1,20}$/, /^\/accept-invitation\?token=[A-Za-z0-9_-]{1,200}$/];

export function safeNext(raw: string | string[] | undefined): string {
  const value = Array.isArray(raw) ? raw[0] : raw;
  if (!value || value.length > 300) return DEFAULT_NEXT;
  return ALLOWED.some((pattern) => pattern.test(value)) ? value : DEFAULT_NEXT;
}

/** Token de convite na URL: so caracteres seguros e tamanho razoavel (a API valida de verdade). */
export function plausibleInviteToken(raw: string | string[] | undefined): string | null {
  return typeof raw === "string" && /^[A-Za-z0-9_-]{16,200}$/.test(raw) ? raw : null;
}
