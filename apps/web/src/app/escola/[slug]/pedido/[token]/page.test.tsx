import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { json, problem } from "@/test-utils/auth-fixtures";
import { RECEIPT_BODY, SCHOOL_BODY, TOKEN } from "@/test-utils/public-fixtures";

const upstream = vi.hoisted(() => ({ handler: null as null | ((url: string) => Response | Promise<Response>) }));
vi.mock("@/lib/api/server", () => ({
  serverFetch: (input: string) => upstream.handler!(String(input)),
}));
vi.mock("next/navigation", () => ({
  notFound: () => {
    throw Object.assign(new Error("NEXT_NOT_FOUND"), { digest: "NEXT_HTTP_ERROR_FALLBACK;404" });
  },
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn(), push: vi.fn() }),
}));

import ReceiptPage from "./page";

const school = () => json(SCHOOL_BODY, 200, { "Content-Type": "application/json" });

function serve(receipt: () => Response | Promise<Response>, schoolResponse: () => Response = school) {
  upstream.handler = (url) => (url.endsWith("/receipt") ? receipt() : schoolResponse());
}

async function html(slug = "demo-aurora", token = TOKEN) {
  return renderToStaticMarkup(await ReceiptPage({ params: Promise.resolve({ slug, token }) }));
}

beforeEach(() => {
  upstream.handler = null;
});

describe("comprovante: HTML do servidor", () => {
  it("200 da API: o servidor entrega o comprovante (Pago) com os dados da API", async () => {
    serve(() => json(RECEIPT_BODY, 200, { "Content-Type": "application/json" }));
    const out = await html();
    expect(out).toContain("Contribuição confirmada");
    expect(out).toContain("APM-000026");
    expect(out).not.toContain("Conferindo seu comprovante");
  });

  it("409 da API: 'ainda nao confirmado', sem 'Pago' e sem valor", async () => {
    serve(() => problem("payment_not_confirmed", 409));
    const out = await html();
    expect(out).toContain("Este pagamento ainda não foi confirmado");
    expect(out).not.toMatch(/\bPago\b/);
    expect(out).not.toMatch(/R\$\s?\d/);
  });

  it("404 da API (token inexistente, de outra escola ou malformado) vira 404 de verdade", async () => {
    serve(() => problem("not_found", 404));
    await expect(html()).rejects.toMatchObject({ digest: expect.stringContaining("404") });
  });

  it("escola inexistente (404 da API) tambem e 404", async () => {
    serve(() => json(RECEIPT_BODY, 200), () => problem("not_found", 404));
    await expect(html()).rejects.toMatchObject({ digest: expect.stringContaining("404") });
  });

  it("falha do servidor ou 429 na consulta do comprovante: HTML NEUTRO (sem 'Pago', sem valor, sem nome)", async () => {
    for (const response of [problem("rate_limited", 429, {}, { "Retry-After": "10" }), problem("internal_error", 500)]) {
      serve(() => response);
      const out = await html();
      expect(out).toContain("Conferindo seu comprovante");
      expect(out).not.toMatch(/\bPago\b|R\$\s?\d/);
    }
  });

  it("API fora do ar na consulta da escola: tela de aviso, sem comprovante", async () => {
    upstream.handler = () => Promise.reject(new TypeError("fetch failed"));
    const out = await html();
    expect(out).toContain("Não foi possível abrir o comprovante");
    expect(out).not.toMatch(/\bPago\b/);
  });

  it("token ou slug em formato invalido: 404 sem chamar a API", async () => {
    const handler = vi.fn();
    upstream.handler = handler;
    for (const [slug, token] of [["demo-aurora", "teste1"], ["demo-aurora", "../etc/passwd"], ["Demo Aurora", TOKEN], ["a--b", TOKEN]] as const) {
      await expect(html(slug, token)).rejects.toMatchObject({ digest: expect.stringContaining("404") });
    }
    expect(handler).not.toHaveBeenCalled();
  });
});
