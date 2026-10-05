import { describe, expect, it, vi } from "vitest";
import { apiGet, apiSend } from "./client";
import { getReadiness } from "./health";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiGet", () => {
  it("devolve os dados quando a resposta e valida", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ status: "ok" }));
    const result = await apiGet("/api/health", (b) => b, { fetchImpl });
    expect(result).toEqual({ ok: true, data: { status: "ok" } });
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/health",
      expect.objectContaining({ method: "GET", cache: "no-store", credentials: "same-origin" }),
    );
  });

  it("so aceita caminhos /api/* da mesma origem", async () => {
    const fetchImpl = vi.fn();
    for (const path of ["https://evil.example/api/x", "//evil.example/api/x", "/painel", "api/health"]) {
      const result = await apiGet(path, (b) => b, { fetchImpl });
      expect(result.ok).toBe(false);
    }
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("devolve erro http quando o parser recusa uma resposta nao-ok", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ detail: "x" }, 500));
    const result = await apiGet("/api/x", () => null, { fetchImpl });
    expect(result).toEqual({ ok: false, error: { kind: "http", status: 500 } });
  });

  it("devolve invalid-response quando 200 vem com corpo inesperado", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("<html>", { status: 200 }));
    const result = await apiGet("/api/x", () => null, { fetchImpl });
    expect(result).toEqual({ ok: false, error: { kind: "invalid-response", status: 200 } });
  });

  it("nao lanca excecao e nao escreve no console quando a rede falha", async () => {
    const spies = [vi.spyOn(console, "error"), vi.spyOn(console, "warn"), vi.spyOn(console, "log")];
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    const result = await apiGet("/api/x", (b) => b, { fetchImpl });
    expect(result).toEqual({ ok: false, error: { kind: "network" } });
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });

  it("interrompe por timeout", async () => {
    const fetchImpl = vi.fn(
      (_url: unknown, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );
    const result = await apiGet("/api/x", (b) => b, { fetchImpl: fetchImpl as unknown as typeof fetch, timeoutMs: 20 });
    expect(result).toEqual({ ok: false, error: { kind: "timeout" } });
  });
});

describe("getReadiness", () => {
  it("200 ready -> ready", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ status: "ready" }));
    expect(await getReadiness({ fetchImpl })).toEqual({ state: "ready" });
  });

  it("503 unavailable -> a API respondeu mas nao esta pronta", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ status: "unavailable" }, 503));
    expect(await getReadiness({ fetchImpl })).toEqual({ state: "unavailable", reason: "not-ready" });
  });

  it("API fora do ar (rede) -> unavailable/unreachable", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    expect(await getReadiness({ fetchImpl })).toEqual({ state: "unavailable", reason: "unreachable" });
  });

  it("500 do proxy sem corpo (API parada atras do rewrite) -> unreachable", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("Internal Server Error", { status: 500 }));
    expect(await getReadiness({ fetchImpl })).toEqual({ state: "unavailable", reason: "unreachable" });
  });

  it("nao confia em status inconsistente (200 com 'unavailable', 503 com 'ready')", async () => {
    const a = vi.fn().mockResolvedValue(jsonResponse({ status: "unavailable" }, 200));
    const b = vi.fn().mockResolvedValue(jsonResponse({ status: "ready" }, 503));
    expect(await getReadiness({ fetchImpl: a })).toEqual({ state: "unavailable", reason: "unreachable" });
    expect(await getReadiness({ fetchImpl: b })).toEqual({ state: "unavailable", reason: "unreachable" });
  });

  it("corpo que nao e JSON -> unreachable", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("nope", { status: 200 }));
    expect(await getReadiness({ fetchImpl })).toEqual({ state: "unavailable", reason: "unreachable" });
  });
});

describe("apiSend", () => {
  const TOKEN = "t".repeat(32);

  it("envia JSON, o cabecalho X-CSRF-Token e usa a mesma origem", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ ok: 1 }));
    const result = await apiSend("POST", "/api/v1/x", { a: 1 }, (b) => b, { fetchImpl, csrfToken: TOKEN });
    expect(result).toEqual({ ok: true, data: { ok: 1 } });
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/api/v1/x");
    expect(init).toMatchObject({ method: "POST", body: '{"a":1}', credentials: "same-origin", cache: "no-store" });
    expect(init.headers).toMatchObject({ "Content-Type": "application/json", "X-CSRF-Token": TOKEN, Accept: "application/json" });
  });

  it("le o token do cookie legivel quando nao e informado, e nao manda cabecalho sem cookie", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({}));
    document.cookie = `apm_csrf=${TOKEN}; path=/`;
    await apiSend("POST", "/api/v1/x", undefined, (b) => b, { fetchImpl });
    expect(fetchImpl.mock.calls[0]![1].headers["X-CSRF-Token"]).toBe(TOKEN);
    expect(fetchImpl.mock.calls[0]![1].body).toBeUndefined();
    expect(fetchImpl.mock.calls[0]![1].headers["Content-Type"]).toBeUndefined();
    document.cookie = "apm_csrf=; Max-Age=0; path=/";
    await apiSend("POST", "/api/v1/x", undefined, (b) => b, { fetchImpl });
    expect(fetchImpl.mock.calls[1]![1].headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("GET nunca leva X-CSRF-Token", async () => {
    document.cookie = `apm_csrf=${TOKEN}; path=/`;
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({}));
    await apiGet("/api/x", (b) => b, { fetchImpl });
    expect(fetchImpl.mock.calls[0]![1].headers["X-CSRF-Token"]).toBeUndefined();
    document.cookie = "apm_csrf=; Max-Age=0; path=/";
  });

  it("so aceita caminhos /api/* e nao chama a rede para os outros", async () => {
    const fetchImpl = vi.fn();
    const result = await apiSend("POST", "https://evil.example/api/x", {}, (b) => b, { fetchImpl });
    expect(result.ok).toBe(false);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("problem+json vira kind 'problem' so com o code; title, detail e request_id do servidor somem", async () => {
    const body = {
      type: "urn:x", title: "Invalid e-mail or password", status: 401, code: "invalid_credentials",
      request_id: "abc", detail: "detalhe em ingles",
    };
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(body, 401));
    const result = await apiSend("POST", "/api/v1/x", {}, () => null, { fetchImpl });
    expect(result).toEqual({ ok: false, error: { kind: "problem", status: 401, code: "invalid_credentials" } });
    expect(JSON.stringify(result)).not.toMatch(/Invalid e-mail|detalhe|abc/);
  });

  it("429: Retry-After em segundos, limitado a uma hora; valor estranho e ignorado", async () => {
    const make = (retry: string) =>
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ code: "rate_limited" }), { status: 429, headers: { "Retry-After": retry } }),
      );
    const a = await apiSend("POST", "/api/v1/x", {}, () => null, { fetchImpl: make("30") });
    const b = await apiSend("POST", "/api/v1/x", {}, () => null, { fetchImpl: make("999999") });
    const c = await apiSend("POST", "/api/v1/x", {}, () => null, { fetchImpl: make("Wed, 21 Oct 2026 07:28:00 GMT") });
    const d = await apiSend("POST", "/api/v1/x", {}, () => null, { fetchImpl: make("-5") });
    expect(a).toMatchObject({ error: { code: "rate_limited", retryAfterSeconds: 30 } });
    expect(b).toMatchObject({ error: { retryAfterSeconds: 3600 } });
    expect(c).toMatchObject({ error: { code: "rate_limited" } });
    expect((c as { error: { retryAfterSeconds?: number } }).error.retryAfterSeconds).toBeUndefined();
    expect((d as { error: { retryAfterSeconds?: number } }).error.retryAfterSeconds).toBeUndefined();
  });

  it("422: guarda so campo e code de cada erro (lista limitada e validada)", async () => {
    const body = {
      code: "weak_password",
      errors: [{ field: "password", code: "too_short" }, { field: 1, code: "x" }, { field: "a", code: "Mensagem Livre!" }, "x"],
    };
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(body, 422));
    const result = await apiSend("POST", "/api/v1/x", {}, () => null, { fetchImpl });
    expect(result).toEqual({
      ok: false,
      error: { kind: "problem", status: 422, code: "weak_password", fields: [{ field: "password", code: "too_short" }] },
    });
  });

  it("code malformado, sem JSON ou sem code vira erro http comum", async () => {
    for (const response of [
      jsonResponse({ code: "<script>" }, 400),
      new Response("Bad Gateway", { status: 502 }),
      jsonResponse({ detail: "x" }, 500),
    ]) {
      const result = await apiSend("POST", "/api/v1/x", {}, () => null, { fetchImpl: vi.fn().mockResolvedValue(response) });
      expect(result).toMatchObject({ ok: false, error: { kind: "http" } });
      expect((result as { error: { code?: string } }).error.code).toBeUndefined();
    }
  });

  it("rede e timeout nao lancam e nao escrevem no console", async () => {
    const spies = [vi.spyOn(console, "error"), vi.spyOn(console, "warn"), vi.spyOn(console, "log")];
    expect(await apiSend("POST", "/api/v1/x", {}, (b) => b, { fetchImpl: vi.fn().mockRejectedValue(new TypeError("x")) })).toEqual({
      ok: false,
      error: { kind: "network" },
    });
    const hang = vi.fn(
      (_u: unknown, init?: RequestInit) =>
        new Promise<Response>((_res, rej) => init?.signal?.addEventListener("abort", () => rej(new DOMException("a", "AbortError")))),
    );
    expect(await apiSend("POST", "/api/v1/x", {}, (b) => b, { fetchImpl: hang as unknown as typeof fetch, timeoutMs: 15 })).toEqual({
      ok: false,
      error: { kind: "timeout" },
    });
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });
});
