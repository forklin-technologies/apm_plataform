import { randomBytes } from "node:crypto";

const ALFANUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";

function aleatorio(n: number): string {
  const bytes = randomBytes(n * 2);
  let s = "";
  for (let i = 0; s.length < n; i++) {
    const b = bytes[i % bytes.length]!;
    if (b < 248) s += ALFANUM[b % 62]; // 248 = 62*4 evita viés
    if (i > n * 8) return s + aleatorio(n - s.length);
  }
  return s;
}

/**
 * txid da cobrança: 26 a 35 caracteres [a-zA-Z0-9] (regra do Banco Central).
 * Prefixo "APM" facilita reconhecer no extrato; 32 caracteres no total.
 */
export function gerarTxid(): string {
  return "APM" + aleatorio(29);
}

export const TXID_VALIDO = /^[a-zA-Z0-9]{26,35}$/;

/** Token do link do pedido (comprovante sem login). */
export function gerarTokenAcesso(): string {
  return randomBytes(24).toString("base64url");
}

/** Segredo que compõe a URL do webhook de cada escola. */
export function gerarSegredoWebhook(): string {
  return aleatorio(40);
}

/** 123 -> "APM-000123" */
export function formatarCodigoPedido(n: number): string {
  return `APM-${n.toString().padStart(6, "0")}`;
}
