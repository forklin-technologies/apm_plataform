import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { json, problem } from "@/test-utils/auth-fixtures";
import { SCHOOL_BODY } from "@/test-utils/public-fixtures";

const upstream = vi.hoisted(() => ({ handler: null as null | ((url: string) => Response | Promise<Response>) }));
vi.mock("@/lib/api/server", () => ({ serverFetch: (input: string) => upstream.handler!(String(input)) }));
vi.mock("next/navigation", () => ({
  notFound: () => {
    throw Object.assign(new Error("NEXT_NOT_FOUND"), { digest: "NEXT_HTTP_ERROR_FALLBACK;404" });
  },
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn(), push: vi.fn() }),
}));

import SchoolPortalPage, { generateMetadata } from "./page";

async function html(slug = "demo-aurora") {
  return renderToStaticMarkup(await SchoolPortalPage({ params: Promise.resolve({ slug }) }));
}

beforeEach(() => {
  upstream.handler = null;
});

describe("/escola/{slug} no servidor", () => {
  it("a escola vem do slug: busca GET /api/v1/public/schools/{slug} e desenha nome e valores da API", async () => {
    const seen: string[] = [];
    upstream.handler = (url) => {
      seen.push(url);
      return json(SCHOOL_BODY, 200, { "Content-Type": "application/json" });
    };
    const out = await html();
    expect(seen).toEqual(["/api/v1/public/schools/demo-aurora"]);
    expect(out).toContain("Escola Aurora (demo)");
    expect(out).toContain("Quanto você quer contribuir?");
    expect(out).toMatch(/R\$\s20,00/);
    expect(out).not.toContain("Protótipo");
    expect((await generateMetadata({ params: Promise.resolve({ slug: "demo-aurora" }) })).title).toBe("Contribuir · Escola Aurora (demo)");
  });

  it("404 da API: 404 de verdade; slug mal formado: 404 sem chamar a API", async () => {
    upstream.handler = () => problem("not_found", 404);
    await expect(html("nao-existe")).rejects.toMatchObject({ digest: expect.stringContaining("404") });
    const handler = vi.fn();
    upstream.handler = handler;
    await expect(html("../etc")).rejects.toMatchObject({ digest: expect.stringContaining("404") });
    expect(handler).not.toHaveBeenCalled();
  });

  it("429: tela amigavel com o tempo; API fora do ar: tela amigavel; nunca texto do servidor", async () => {
    upstream.handler = () => problem("rate_limited", 429, {}, { "Retry-After": "90" });
    const limited = await html();
    expect(limited).toContain("Muitos acessos agora");
    expect(limited).toContain("2 minutos");
    upstream.handler = () => Promise.reject(new TypeError("fetch failed"));
    const down = await html();
    expect(down).toContain("Não foi possível abrir esta página");
    expect(down).not.toMatch(/Texto em ingles|internal/i);
  });
});
