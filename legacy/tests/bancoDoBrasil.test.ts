import { describe, expect, it } from "vitest";
import { BancoDoBrasilProvider } from "@/pix/bancoDoBrasil";
import type { Transporte } from "@/pix/bacen";

function transporteFalso(respostas: ((url: string, init: any) => { status: number; corpo: unknown })[]) {
  const chamadas: { url: string; init: any }[] = [];
  const t: Transporte = async (url, init) => {
    chamadas.push({ url, init });
    const r = respostas.shift()!(url, init);
    return { status: r.status, texto: typeof r.corpo === "string" ? r.corpo : JSON.stringify(r.corpo) };
  };
  return { t, chamadas };
}

const token = () => ({ status: 200, corpo: { access_token: "tok", expires_in: 600 } });
const cfg = { ambiente: "homologacao" as const, clientId: "cid", clientSecret: "csec", appKey: "chave-app", chavePix: "03604104000101" };

describe("Banco do Brasil – API Pix v2", () => {
  it("cria cobrança imediata com valor fixo, chave CNPJ e chave de aplicação", async () => {
    const { t, chamadas } = transporteFalso([
      token,
      () => ({ status: 201, corpo: { txid: "APMabcdefghijklmnopqrstuvwxyz12", status: "ATIVA", valor: { original: "20.00" },
        pixCopiaECola: "00020101021226...6304ABCD" } }),
    ]);
    const bb = new BancoDoBrasilProvider(cfg, t);
    const r = await bb.criarCobranca({ txid: "APMabcdefghijklmnopqrstuvwxyz12", valorCentavos: 2000, expiracaoSegundos: 1800,
      solicitacaoPagador: "APM – pedido APM-000001" });
    expect(r.pixCopiaECola).toContain("000201");

    const [auth, cob] = chamadas;
    expect(auth!.init.headers.Authorization).toBe("Basic " + Buffer.from("cid:csec").toString("base64"));
    expect(auth!.init.body).toContain("grant_type=client_credentials");
    expect(cob!.init.method).toBe("PUT");
    expect(cob!.url).toMatch(/\/pix\/v2\/cob\/APMabcdefghijklmnopqrstuvwxyz12\?gw-dev-app-key=chave-app$/);
    const corpo = JSON.parse(cob!.init.body);
    expect(corpo).toMatchObject({ calendario: { expiracao: 1800 }, valor: { original: "20.00", modalidadeAlteracao: 0 },
      chave: "03604104000101" });
    expect(cob!.init.headers.Authorization).toBe("Bearer tok");
  });

  it("recusa resposta do banco com valor diferente do pedido", async () => {
    const { t } = transporteFalso([token, () => ({ status: 201, corpo: { txid: "x".repeat(26), status: "ATIVA",
      valor: { original: "2.00" }, pixCopiaECola: "000201" } })]);
    await expect(new BancoDoBrasilProvider(cfg, t).criarCobranca({ txid: "x".repeat(26), valorCentavos: 2000,
      expiracaoSegundos: 60, solicitacaoPagador: "t" })).rejects.toThrow(/valor diferente/);
  });

  it("consulta cobrança concluída e renova token revogado (401)", async () => {
    const { t, chamadas } = transporteFalso([
      token,
      () => ({ status: 401, corpo: "" }),
      token,
      () => ({ status: 200, corpo: { txid: "T".repeat(30), status: "CONCLUIDA", valor: { original: "45.00" },
        pix: [{ endToEndId: "E0000000020260927", txid: "T".repeat(30), valor: "45.00", horario: "2026-09-27T12:00:00.000Z",
          pagador: { nome: "MARIA SILVA" } }] } }),
    ]);
    const r = await new BancoDoBrasilProvider(cfg, t).consultarCobranca("T".repeat(30));
    expect(r.status).toBe("CONCLUIDA");
    expect(r.pagamentos[0]).toMatchObject({ valorCentavos: 4500, pagadorNome: "MARIA SILVA" });
    expect(chamadas).toHaveLength(4);
  });

  it("interpreta webhook no padrão Bacen e ignora corpo malformado", () => {
    const bb = new BancoDoBrasilProvider(cfg, async () => ({ status: 500, texto: "" }));
    expect(bb.interpretarWebhook({ pix: [{ endToEndId: "E1", txid: "T1", valor: "10.00", horario: "2026-09-27T12:00:00Z" }] }))
      .toEqual([{ endToEndId: "E1", txid: "T1" }]);
    expect(bb.interpretarWebhook({ qualquer: 1 })).toEqual([]);
  });

  it("erro do banco vira ErroProvedorPix com status", async () => {
    const { t } = transporteFalso([token, () => ({ status: 400, corpo: { title: "Cobrança inválida" } })]);
    await expect(new BancoDoBrasilProvider(cfg, t).consultarCobranca("z".repeat(26))).rejects.toMatchObject({ httpStatus: 400 });
  });
});
