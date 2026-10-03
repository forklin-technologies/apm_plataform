import { expect, test } from "@playwright/test";

/** N3b e N11: a CSP com nonce chega a todas as paginas (inclusive /apix) e o favicon existe. */
const nonce = (csp: string) => /'nonce-([^']+)'/.exec(csp)?.[1];

test("N3b: /apix, /api-docs e /apiary NAO ficam de fora do proxy: recebem a CSP com nonce", async ({ request }) => {
  const seen = new Set<string>();
  for (const path of ["/apix", "/api-docs", "/apiary", "/apis/x"]) {
    const response = await request.get(path);
    expect(response.status(), path).toBe(404);
    const csp = response.headers()["content-security-policy"] ?? "";
    expect(csp, path).toMatch(/script-src 'self' 'nonce-[^']+' 'strict-dynamic'/);
    expect(csp, path).toContain("frame-ancestors 'none'");
    expect(csp, path).not.toContain("'unsafe-eval'");
    seen.add(nonce(csp)!);
  }
  expect(seen.size, "um nonce novo por requisicao").toBe(4);
});

test("as paginas normais continuam com a CSP e os cabecalhos estaticos", async ({ request }) => {
  for (const path of ["/", "/login", "/painel", "/apm/escola-exemplo"]) {
    const headers = (await request.get(path)).headers();
    expect(headers["content-security-policy"], path).toContain("object-src 'none'");
    expect(headers["x-content-type-options"], path).toBe("nosniff");
    expect(headers["referrer-policy"], path).toBe("no-referrer");
    expect(headers["x-frame-options"], path).toBe("DENY");
    expect(headers["x-powered-by"], path).toBeUndefined();
  }
});

test("/api e /api/* continuam fora do proxy de CSP (a resposta e da API, via rewrite)", async ({ request }) => {
  const response = await request.get("/api/health/ready");
  expect([200, 500, 503]).toContain(response.status());
  expect(response.headers()["content-security-policy"]).toBeUndefined();
});

test("N11: /favicon.ico e /icon.svg existem (o Safari nao usa SVG e pedia o .ico)", async ({ request }) => {
  const ico = await request.get("/favicon.ico");
  expect(ico.status()).toBe(200);
  expect(ico.headers()["content-type"]).toMatch(/image\/(x-icon|vnd\.microsoft\.icon)/);
  expect((await ico.body()).length).toBeGreaterThan(200);
  expect((await request.get("/icon.svg")).status()).toBe(200);
});

test("N11: a pagina declara os icones e nao gera 404 de favicon no navegador", async ({ page }) => {
  const failed: string[] = [];
  page.on("response", (r) => {
    if (r.status() >= 400) failed.push(`${r.status()} ${new URL(r.url()).pathname}`);
  });
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  expect(failed).toEqual([]);
});
