import { beforeEach, describe, expect, it, vi } from "vitest";
import { json, problem, sessionBody } from "@/test-utils/auth-fixtures";

const jar = vi.hoisted(() => ({ values: new Map<string, string>() }));
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (jar.values.has(name) ? { name, value: jar.values.get(name)! } : undefined),
  }),
}));

import { getServerSession, serverApiOrigin } from "./server";

const SESSION_VALUE = "s".repeat(43);

beforeEach(() => {
  jar.values.clear();
});

describe("serverApiOrigin", () => {
  it("usa API_PROXY_URL (so a origem) e cai no padrao quando invalida", () => {
    expect(serverApiOrigin("http://api.internal:9000/qualquer/caminho")).toBe("http://api.internal:9000");
    expect(serverApiOrigin("ftp://x")).toBe("http://127.0.0.1:8001");
    expect(serverApiOrigin("nao e url")).toBe("http://127.0.0.1:8001");
    expect(serverApiOrigin(undefined)).toBe("http://127.0.0.1:8001");
  });
});

describe("getServerSession", () => {
  it("sem cookie de sessao: anonimo, sem chamar a API", async () => {
    jar.values.set("apm_csrf", "x".repeat(40)); // o cookie CSRF nao e sessao
    const fetchImpl = vi.fn();
    expect(await getServerSession(fetchImpl)).toEqual({ state: "anonymous" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("repassa so o cookie de sessao a GET /api/v1/auth/me e devolve a sessao", async () => {
    jar.values.set("apm_session", SESSION_VALUE);
    jar.values.set("apm_csrf", "x".repeat(40));
    jar.values.set("outro", "nao-repassar");
    const fetchImpl = vi.fn().mockResolvedValue(json(sessionBody()));
    const result = await getServerSession(fetchImpl);
    expect(result.state).toBe("authenticated");
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("http://127.0.0.1:8001/api/v1/auth/me");
    expect(init.method).toBe("GET");
    expect(init.headers.Cookie).toBe(`apm_session=${SESSION_VALUE}`);
    expect(init.cache).toBe("no-store");
  });

  it("repassa o nome com prefixo __Host- (producao)", async () => {
    jar.values.set("__Host-apm_session", SESSION_VALUE);
    const fetchImpl = vi.fn().mockResolvedValue(json(sessionBody()));
    await getServerSession(fetchImpl);
    expect(fetchImpl.mock.calls[0]![1].headers.Cookie).toBe(`__Host-apm_session=${SESSION_VALUE}`);
  });

  it("401 (sessao ausente, expirada ou revogada) e anonimo", async () => {
    jar.values.set("apm_session", SESSION_VALUE);
    for (const code of ["unauthenticated", "session_revoked"]) {
      const fetchImpl = vi.fn().mockResolvedValue(problem(code, 401));
      expect(await getServerSession(fetchImpl)).toEqual({ state: "anonymous" });
    }
  });

  it("API fora do ar, 5xx ou corpo estranho NAO e logout: 'unavailable'", async () => {
    jar.values.set("apm_session", SESSION_VALUE);
    expect(await getServerSession(vi.fn().mockRejectedValue(new TypeError("fetch failed")))).toEqual({ state: "unavailable" });
    expect(await getServerSession(vi.fn().mockResolvedValue(problem("internal_error", 500)))).toEqual({ state: "unavailable" });
    expect(await getServerSession(vi.fn().mockResolvedValue(new Response("<html>", { status: 200 })))).toEqual({ state: "unavailable" });
    expect(await getServerSession(vi.fn().mockResolvedValue(json({ foo: 1 })))).toEqual({ state: "unavailable" });
  });
});
