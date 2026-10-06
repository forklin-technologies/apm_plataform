import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/painel/PainelPage", () => ({ PainelPage: (props: unknown) => ({ props }) }));

import Page from "./page";

describe("/painel (rota)", () => {
  it("entrega o resumo e repassa tipo e mes da URL", async () => {
    const element = (await Page({ searchParams: Promise.resolve({ tipo: "despesas", mes: "2026-09" }) })) as unknown as { props: Record<string, unknown> };
    expect(element.props).toMatchObject({ area: "resumo", tipo: "despesas", mes: "2026-09" });
  });
});
