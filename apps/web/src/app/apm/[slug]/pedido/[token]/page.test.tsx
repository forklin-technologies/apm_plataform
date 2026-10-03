import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DEMO_RECEIPT_TOKEN } from "@/lib/api/token";
import { randomToken } from "@/mocks/util";
import ReceiptPage from "./page";

/**
 * N1: o HTML que o SERVIDOR entrega (o que um curl ou um rastreador ve, sem JavaScript) nunca
 * afirma "Pago" nem mostra dado de pessoa para um token que a camada de dados nao confirmou no
 * servidor. So o link de exemplo tem comprovante fixo.
 */
async function serverHtml(slug: string, token: string): Promise<string> {
  const element = await ReceiptPage({ params: Promise.resolve({ slug, token }) });
  return renderToStaticMarkup(element);
}

describe("comprovante: HTML do servidor", () => {
  it("qualquer token que so existe no navegador sai NEUTRO: sem 'Pago', sem nome, sem valor", async () => {
    for (let i = 0; i < 25; i += 1) {
      const html = await serverHtml("escola-exemplo", randomToken());
      expect(html).toContain("Conferindo seu comprovante");
      expect(html).not.toMatch(/\bPago\b/);
      expect(html).not.toMatch(/Pagamento confirmado|Contribuição confirmada/);
      expect(html).not.toMatch(/R\$\s?\d/); // nenhum valor
      expect(html).not.toContain("Nome do responsável");
      expect(html).not.toContain("Nome do aluno");
    }
  });

  it("tokens plausiveis arbitrarios (inclusive de 22 a 64 caracteres) tambem saem neutros", async () => {
    for (const token of ["A".repeat(22), "b".repeat(64), "Pago_Pago_Pago_Pago_Pago_", "demo-comprovante-0002"].filter((t) => t.length >= 22)) {
      const html = await serverHtml("escola-horizonte", token);
      expect(html).toContain("Conferindo seu comprovante");
      expect(html.replace(/Pago_/g, "")).not.toMatch(/\bPago\b/);
    }
  });

  it("so o link de exemplo renderiza o comprovante fixo no servidor", async () => {
    const html = await serverHtml("escola-exemplo", DEMO_RECEIPT_TOKEN);
    expect(html).toContain("Contribuição confirmada");
    expect(html).toMatch(/\bPago\b/);
    expect(html).not.toContain("Conferindo seu comprovante");
  });

  it("token em formato invalido ou escola inexistente e 404 de verdade (notFound)", async () => {
    for (const [slug, token] of [
      ["escola-exemplo", "teste1"],
      ["escola-exemplo", "../etc/passwd"],
      ["escola-exemplo", "a".repeat(21)],
      ["escola-que-nao-existe", randomToken()],
    ] as const) {
      await expect(serverHtml(slug, token)).rejects.toMatchObject({ digest: expect.stringContaining("404") });
    }
  });
});
