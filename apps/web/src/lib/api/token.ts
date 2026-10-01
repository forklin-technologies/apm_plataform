/**
 * Token do comprovante: opaco, seguro para URL (PROPOSTA de contrato: >= 128 bits no backend real).
 * O frontend so confere o formato (4 a 64 caracteres seguros para URL) para evitar requisicoes
 * obviamente invalidas; quem decide se o token existe e o backend.
 */
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{4,64}$/;

export function isPlausibleToken(value: string): boolean {
  return TOKEN_PATTERN.test(value);
}

const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function isPlausibleSlug(value: string): boolean {
  return value.length <= 80 && SLUG_PATTERN.test(value);
}
