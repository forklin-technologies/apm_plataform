/**
 * Token do comprovante: opaco e seguro para URL (a API gera 256 bits, 43 caracteres em base64url).
 * O frontend so confere o FORMATO (22 a 64 caracteres seguros para URL) para barrar requisicoes
 * obviamente invalidas. Quem decide se o token existe e a API: o frontend nunca presume que um
 * token plausivel e valido (a API responde o mesmo 404 para qualquer token ruim).
 */
export const MIN_TOKEN_LENGTH = 22;
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{22,64}$/;

export function isPlausibleToken(value: string): boolean {
  return TOKEN_PATTERN.test(value);
}

const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function isPlausibleSlug(value: string): boolean {
  return value.length <= 80 && SLUG_PATTERN.test(value);
}
