/**
 * Token do comprovante: opaco e seguro para URL. No backend real tem >= 128 bits de entropia
 * (PROPOSTA de contrato). 128 bits em base64url = 22 caracteres; o frontend so confere o FORMATO
 * (22 a 64 caracteres seguros para URL) para barrar requisicoes obviamente invalidas. Quem decide
 * se o token existe e a camada de dados: o frontend nunca presume que um token plausivel e valido.
 */
export const MIN_TOKEN_LENGTH = 22;
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{22,64}$/;

/** Link de exemplo usado pela entrada: o UNICO token com comprovante de exemplo fixo. */
export const DEMO_RECEIPT_TOKEN = "demo-comprovante-0001";

export function isPlausibleToken(value: string): boolean {
  return value === DEMO_RECEIPT_TOKEN || TOKEN_PATTERN.test(value);
}

const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function isPlausibleSlug(value: string): boolean {
  return value.length <= 80 && SLUG_PATTERN.test(value);
}
