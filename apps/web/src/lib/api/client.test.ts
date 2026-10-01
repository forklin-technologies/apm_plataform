import { describe, expect, it, vi } from "vitest";
import { apiGet } from "./client";
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
