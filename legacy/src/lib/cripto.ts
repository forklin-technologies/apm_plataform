/**
 * Criptografia simétrica (AES-256-GCM) para segredos guardados no banco:
 * credenciais Pix, certificado mTLS, nome do pagador em contribuições anônimas.
 * A chave mestra vem de APP_ENCRYPTION_KEY e nunca é gravada no banco.
 */
import { createCipheriv, createDecipheriv, randomBytes, createHash, timingSafeEqual } from "node:crypto";

function chave(): Buffer {
  const b64 = process.env.APP_ENCRYPTION_KEY;
  if (!b64) throw new Error("APP_ENCRYPTION_KEY não definida");
  const k = Buffer.from(b64, "base64");
  if (k.length !== 32) throw new Error("APP_ENCRYPTION_KEY deve ter 32 bytes (base64)");
  return k;
}

const VERSAO = "v1";

export function cifrarBytes(dados: Buffer): string {
  const iv = randomBytes(12);
  const c = createCipheriv("aes-256-gcm", chave(), iv);
  const corpo = Buffer.concat([c.update(dados), c.final()]);
  return [VERSAO, iv.toString("base64"), c.getAuthTag().toString("base64"), corpo.toString("base64")].join(":");
}

export function decifrarBytes(pacote: string): Buffer {
  const [v, iv, tag, corpo] = pacote.split(":");
  if (v !== VERSAO || !iv || !tag || !corpo) throw new Error("Formato cifrado inválido");
  const d = createDecipheriv("aes-256-gcm", chave(), Buffer.from(iv, "base64"));
  d.setAuthTag(Buffer.from(tag, "base64"));
  return Buffer.concat([d.update(Buffer.from(corpo, "base64")), d.final()]);
}

export const cifrar = (texto: string) => cifrarBytes(Buffer.from(texto, "utf8"));
export const decifrar = (pacote: string) => decifrarBytes(pacote).toString("utf8");

export const sha256 = (s: string) => createHash("sha256").update(s).digest("hex");

/** Comparação em tempo constante (segredos de webhook, tokens). */
export function iguaisSeguro(a: string, b: string): boolean {
  const x = Buffer.from(sha256(a)); const y = Buffer.from(sha256(b));
  return timingSafeEqual(x, y);
}
