/** Utilidades das simulacoes. Nada daqui vai para producao. */

const isBrowser = () => typeof window !== "undefined";

/** Latencia simulada so no navegador. No servidor (SSR) responde na hora. */
export function delay(ms: number): Promise<void> {
  if (!isBrowser() || ms <= 0) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export { hashString } from "@/lib/hash";

/**
 * Token de 128 bits (16 bytes de crypto.getRandomValues) em base64url: 22 caracteres.
 * Sem modulo sobre o alfabeto (cada 6 bits viram um simbolo), portanto sem vies.
 */
export function randomToken(): string {
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
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
