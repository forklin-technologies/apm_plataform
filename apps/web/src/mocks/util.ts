/** Utilidades das simulacoes. Nada daqui vai para producao. */

const isBrowser = () => typeof window !== "undefined";

/** Latencia simulada so no navegador. No servidor (SSR) responde na hora. */
export function delay(ms: number): Promise<void> {
  if (!isBrowser() || ms <= 0) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export { hashString } from "@/lib/hash";

const TOKEN_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789";

export function randomToken(length = 20): string {
  const bytes = new Uint8Array(length);
  globalThis.crypto.getRandomValues(bytes);
  let out = "";
  for (const byte of bytes) out += TOKEN_ALPHABET[byte % TOKEN_ALPHABET.length];
  return out;
}

export function readSession<T>(key: string, fallback: T): T {
  if (!isBrowser()) return fallback;
  try {
    const raw = window.sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function writeSession(key: string, value: unknown): void {
  if (!isBrowser()) return;
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    // sessionStorage bloqueado: o prototipo continua so em memoria.
  }
}
