/** Utilidades das simulacoes. Nada daqui vai para producao. */

const isBrowser = () => typeof window !== "undefined";

/** Latencia simulada so no navegador. No servidor (SSR) responde na hora. */
export function delay(ms: number): Promise<void> {
  if (!isBrowser() || ms <= 0) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, ms));
}
