import { cookies } from "next/headers";
import { parseSession } from "./auth";
import type { Session } from "./types";

/**
 * Leitura da sessao NO SERVIDOR do Next (paginas /painel e /accept-invitation): repassa o cookie
 * de sessao a GET /api/v1/auth/me. O navegador continua sem ler esse cookie (HttpOnly); aqui ele
 * so passa de um servidor para o outro. Sem `NEXT_PUBLIC_`: a URL da API nao vai ao navegador.
 */
const SESSION_COOKIES = ["__Host-apm_session", "apm_session"] as const;
const DEFAULT_API_ORIGIN = "http://127.0.0.1:8001";
const TIMEOUT_MS = 5000;

export type ServerSession =
  | { state: "authenticated"; session: Session }
  | { state: "anonymous" }
  | { state: "unavailable" };

export function serverApiOrigin(raw: string | undefined = process.env.API_PROXY_URL): string {
  try {
    const url = new URL(raw?.trim() || DEFAULT_API_ORIGIN);
    return url.protocol === "http:" || url.protocol === "https:" ? url.origin : DEFAULT_API_ORIGIN;
  } catch {
    return DEFAULT_API_ORIGIN;
  }
}

export async function getServerSession(fetchImpl: typeof fetch = fetch): Promise<ServerSession> {
  const jar = await cookies();
  const pairs = SESSION_COOKIES.flatMap((name) => {
    const value = jar.get(name)?.value;
    return value ? [`${name}=${value}`] : [];
  });
  if (pairs.length === 0) return { state: "anonymous" };

  try {
    const response = await fetchImpl(`${serverApiOrigin()}/api/v1/auth/me`, {
      method: "GET",
      headers: { Accept: "application/json", Cookie: pairs.join("; ") },
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (response.status === 401) return { state: "anonymous" };
    if (response.status !== 200) return { state: "unavailable" };
    const session = parseSession(await response.json());
    return session ? { state: "authenticated", session } : { state: "unavailable" };
  } catch {
    return { state: "unavailable" };
  }
}

/**
 * fetch do servidor do Next para a API: o mesmo cliente tipado (src/lib/api/public.ts) funciona nas
 * paginas de servidor se o caminho `/api/...` ganhar a origem da API. Sem cookies: so rotas publicas.
 */
export const serverFetch: typeof fetch = (input, init) => fetch(`${serverApiOrigin()}${String(input)}`, init);
