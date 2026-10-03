import { readFileSync } from "node:fs";
import { join } from "node:path";
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { config, proxy } from "./proxy";

/** O source do matcher e uma regex em um grupo: serve direto como RegExp ancorada. */
const matcher = new RegExp(`^${(config.matcher[0] as { source: string }).source}$`);

describe("N3b: matcher do proxy (CSP com nonce)", () => {
  it("as paginas passam pelo proxy, inclusive as que COMECAM com 'api' mas nao sao /api", () => {
    for (const path of ["/", "/painel", "/login", "/apm/escola-exemplo", "/apix", "/api-docs", "/apiary", "/apis", "/api2/x", "/apix/health"]) {
      expect(matcher.test(path), path).toBe(true);
    }
  });

  it("so /api, /api/* e os estaticos ficam de fora", () => {
    for (const path of ["/api", "/api/", "/api/health", "/api/health/ready", "/_next/static/chunks/a.js", "/_next/image", "/favicon.ico", "/icon.svg"]) {
      expect(matcher.test(path), path).toBe(false);
    }
  });

  it("favicon.ico e icon.svg sao literais (o ponto nao e curinga)", () => {
    expect(matcher.test("/faviconXico")).toBe(true);
    expect(matcher.test("/iconXsvg")).toBe(true);
  });
});

describe("proxy(): CSP com nonce por requisicao", () => {
  const csp = (path: string) => proxy(new NextRequest(`http://127.0.0.1:3100${path}`)).headers.get("content-security-policy") ?? "";

  it("gera nonce, sem unsafe-eval nem unsafe-inline em script, e frame-ancestors none", () => {
    const header = csp("/apix");
    expect(header).toMatch(/script-src 'self' 'nonce-[A-Za-z0-9+/=]+' 'strict-dynamic'/);
    expect(header).not.toContain("'unsafe-eval'");
    expect(header).not.toMatch(/script-src[^;]*'unsafe-inline'/);
    expect(header).toContain("frame-ancestors 'none'");
    expect(header).toContain("object-src 'none'");
    expect(header).toContain("connect-src 'self'");
  });

  it("o nonce muda a cada requisicao", () => {
    const nonce = (h: string) => /'nonce-([^']+)'/.exec(h)![1];
    const nonces = new Set(Array.from({ length: 50 }, () => nonce(csp("/")) ));
    expect(nonces.size).toBe(50);
  });
});

describe("N7: o servidor de producao liga so em 127.0.0.1", () => {
  const pkg = JSON.parse(readFileSync(join(__dirname, "..", "package.json"), "utf8")) as { scripts: Record<string, string> };

  it("o script start embute -H 127.0.0.1 (npm run start nao abre a rede sozinho)", () => {
    expect(pkg.scripts.start).toMatch(/\bnext start\b.*-H 127\.0\.0\.1/);
  });

  it("o script dev tambem", () => {
    expect(pkg.scripts.dev).toMatch(/-H 127\.0\.0\.1/);
  });
});
