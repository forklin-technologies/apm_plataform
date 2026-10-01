/**
 * Token do comprovante: opaco, seguro para URL (PROPOSTA de contrato).
 * O frontend so confere o formato para evitar requisicoes obviamente invalidas.
 */
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{8,64}$/;

export function isPlausibleToken(value: string): boolean {
  return TOKEN_PATTERN.test(value);
}

const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function isPlausibleSlug(value: string): boolean {
  return value.length <= 80 && SLUG_PATTERN.test(value);
}
