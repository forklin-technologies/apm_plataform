/**
 * Testes de integração com PostgreSQL real (papel sem privilégios + RLS).
 */
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { eq, sql } from "drizzle-orm";
import { comEscola, comoSistema, encerrarDb } from "@/db/cliente";
import { campanhas, credenciaisPix, escolas, itens, pagamentos, pedidos, pixAvulsos, turmas, eventosWebhook } from "@/db/schema";
import { semearEscolaPiloto, CAMPOS_PADRAO } from "@/db/seed-dados";
import { criarPedido } from "@/services/pedidos";
import { processarCobranca } from "@/services/confirmacao";
import { verificarPendentes, conciliarPeriodo } from "@/services/rotinas";
import { pedidoPorToken } from "@/services/consultas";
import { SandboxProvider } from "@/pix/sandbox";
import { provedoresInjetados } from "@/pix/fabrica";
import { ErroProvedorPix } from "@/lib/erros";
import { POST as webhook } from "@/app/api/webhooks/pix/[escolaId]/[segredo]/[[...resto]]/route";
import { limparDados, recriarBanco } from "./apoio";

const SLUG = "emeb-aparecida-merino-elias";
let escolaId: string;
let turmaId: string;
let cota20: string, livre: string, mensal: string;

const ident = () => ({ responsavelNome: "Maria Silva", alunoNome: "João Silva", turmaId, responsavelEmail: "maria@exemplo.com" });

async function criarProduto(estoque: number, limite?: number) {
  return comEscola(escolaId, async (tx) => {
    const [c] = await tx.insert(campanhas).values({ escolaId, slug: `c${Date.now()}${Math.random()}`, nome: "Dia dos Pais",
      status: "ATIVA", dataInicio: new Date(Date.now() - 1000) }).returning();
    const [i] = await tx.insert(itens).values({ escolaId, campanhaId: c!.id, tipo: "PRODUTO", nome: "Caneca",
      precoCentavos: 2500, controlaEstoque: true, estoqueTotal: estoque, limitePorPedido: limite }).returning();
    return i!.id;
  });
}
const estoqueDe = (id: string) => comEscola(escolaId, async (tx) => (await tx.select().from(itens).where(eq(itens.id, id)))[0]!);
const txidDe = (token: string) => comEscola(escolaId, async (tx) => (await tx.select({ t: pagamentos.txid }).from(pagamentos)
  .innerJoin(pedidos, eq(pedidos.id, pagamentos.pedidoId)).where(eq(pedidos.tokenAcesso, token)))[0]!.t);

beforeAll(async () => { await recriarBanco(); });
afterAll(async () => { await encerrarDb(); });
beforeEach(async () => {
  await limparDados();
  SandboxProvider.limpar();
  provedoresInjetados.clear();
  const e = await semearEscolaPiloto();
  escolaId = e.id;
  await comEscola(escolaId, async (tx) => {
    turmaId = (await tx.select().from(turmas).where(eq(turmas.nome, "5º A")))[0]!.id;
    const is = await tx.select().from(itens);
    cota20 = is.find((i) => i.nome === "Cota de R$ 20")!.id;
    livre = is.find((i) => i.tipo === "VALOR_LIVRE")!.id;
    mensal = is.find((i) => i.tipo === "CONTRIBUICAO_MENSAL")!.id;
  });
});

describe("criação de pedido", () => {
  it("calcula o total no servidor e ignora preço enviado pelo navegador", async () => {
    const p = await criarPedido(SLUG, { itens: [{ itemId: cota20, quantidade: 2, precoCentavos: 1 }, { itemId: livre, valorCentavos: 750 }],
      identificacao: ident() });
    expect(p.totalCentavos).toBe(2 * 2000 + 750);
    expect(p.codigo).toBe("APM-000001");
    expect(p.pixCopiaECola).toMatch(/^SANDBOX-NAO-PAGAVEL/);
    const detalhe = await pedidoPorToken(SLUG, p.tokenAcesso);
    expect(detalhe!.status).toBe("PIX_GERADO");
    expect(detalhe!.itens).toHaveLength(2);
  });

  it("numeração sequencial por escola", async () => {
    await criarPedido(SLUG, { itens: [{ itemId: cota20 }], identificacao: ident() });
    const b = await criarPedido(SLUG, { itens: [{ itemId: cota20 }], identificacao: ident() });
    expect(b.codigo).toBe("APM-000002");
  });

  it("contribuição mensal: uma linha por mês; mês repetido é recusado", async () => {
    const p = await criarPedido(SLUG, { itens: [{ itemId: mensal, meses: ["2026-09", "2026-10"] }], identificacao: ident() });
    const d = await pedidoPorToken(SLUG, p.tokenAcesso);
    expect(d!.itens.map((i) => i.descricao)).toEqual(["Contribuição mensal APM – setembro/2026", "Contribuição mensal APM – outubro/2026"]);
    await expect(criarPedido(SLUG, { itens: [{ itemId: mensal, meses: ["2026-09", "2026-09"] }], identificacao: ident() }))
      .rejects.toMatchObject({ codigo: "MES_REPETIDO" });
  });

  it("respeita valor mínimo do 'outro valor' e campos obrigatórios", async () => {
    await expect(criarPedido(SLUG, { itens: [{ itemId: livre, valorCentavos: 100 }], identificacao: ident() }))
      .rejects.toMatchObject({ codigo: "VALOR_MINIMO" });
    await expect(criarPedido(SLUG, { itens: [{ itemId: cota20 }], identificacao: { responsavelNome: "Maria" } }))
      .rejects.toMatchObject({ codigo: "CAMPO_OBRIGATORIO" });
  });

  it("contribuição anônima só quando a escola permite, e sem guardar dados pessoais", async () => {
    await expect(criarPedido(SLUG, { itens: [{ itemId: cota20 }], anonimo: true })).rejects.toMatchObject({ codigo: "ANONIMO_NAO_PERMITIDO" });
    await comEscola(escolaId, (tx) => tx.update(escolas).set({ modoIdentificacao: "ANONIMA" }).where(eq(escolas.id, escolaId)));
    const p = await criarPedido(SLUG, { itens: [{ itemId: cota20 }], anonimo: true, identificacao: ident() });
    const [row] = await comEscola(escolaId, (tx) => tx.select().from(pedidos).where(eq(pedidos.tokenAcesso, p.tokenAcesso)));
    expect(row).toMatchObject({ anonimo: true, responsavelNome: null, alunoNome: null, responsavelEmail: null });
  });

  it("item de campanha em rascunho não pode ser comprado", async () => {
    const caneca = await comEscola(escolaId, async (tx) => (await tx.select().from(itens).where(eq(itens.nome, "Caneca personalizada")))[0]!.id);
    await expect(criarPedido(SLUG, { itens: [{ itemId: caneca }], identificacao: ident() })).rejects.toMatchObject({ codigo: "ITEM_INDISPONIVEL" });
  });

  it("falha do banco ao gerar Pix cancela o pedido e devolve o estoque", async () => {
    const prod = await criarProduto(5);
    const falho = new SandboxProvider(escolaId);
    falho.criarCobranca = async () => { throw new ErroProvedorPix("fora do ar", 503); };
    provedoresInjetados.set(escolaId, falho);
    await expect(criarPedido(SLUG, { itens: [{ itemId: prod, quantidade: 2 }], identificacao: ident() }))
      .rejects.toMatchObject({ codigo: "PIX_INDISPONIVEL" });
    expect((await estoqueDe(prod)).estoqueReservado).toBe(0);
    const [ped] = await comEscola(escolaId, (tx) => tx.select().from(pedidos));
    expect(ped!.status).toBe("CANCELADO");
  });
});

describe("estoque", () => {
  it("disputa pelo último item: só um pedido consegue", async () => {
    const prod = await criarProduto(1);
    const r = await Promise.allSettled(Array.from({ length: 6 }, () =>
      criarPedido(SLUG, { itens: [{ itemId: prod }], identificacao: ident() })));
    expect(r.filter((x) => x.status === "fulfilled")).toHaveLength(1);
    expect(r.filter((x) => x.status === "rejected").every((x) => (x as PromiseRejectedResult).reason.codigo === "ESGOTADO")).toBe(true);
    expect(await estoqueDe(prod)).toMatchObject({ estoqueReservado: 1, estoqueVendido: 0 });
  });

  it("limite por pedido", async () => {
    const prod = await criarProduto(10, 2);
    await expect(criarPedido(SLUG, { itens: [{ itemId: prod, quantidade: 1 }, { itemId: prod, quantidade: 2 }], identificacao: ident() }))
      .rejects.toMatchObject({ codigo: "LIMITE_POR_PEDIDO" });
  });

  it("pagamento converte reserva em venda; expiração libera a reserva", async () => {
    const prod = await criarProduto(3);
    const pago = await criarPedido(SLUG, { itens: [{ itemId: prod }], identificacao: ident() });
    const vencido = await criarPedido(SLUG, { itens: [{ itemId: prod }], identificacao: ident() });
    SandboxProvider.simularPagamento(await txidDe(pago.tokenAcesso));
    expect(await processarCobranca(escolaId, await txidDe(pago.tokenAcesso), "teste")).toBe("confirmado");

    await comoSistema((tx) => tx.execute(sql`update pagamentos set expira_em = now() - interval '1 hour'
      where txid = ${"x"} or pedido_id in (select id from pedidos where token_acesso = ${vencido.tokenAcesso})`));
    const r = await verificarPendentes();
    expect(r.expirados).toBe(1);
    expect(await estoqueDe(prod)).toMatchObject({ estoqueReservado: 0, estoqueVendido: 1 });
    expect((await pedidoPorToken(SLUG, vencido.tokenAcesso))!.status).toBe("EXPIRADO");
  });
});

describe("confirmação de pagamento", () => {
  it("é idempotente: o mesmo aviso duas vezes não duplica nada", async () => {
    const p = await criarPedido(SLUG, { itens: [{ itemId: cota20 }], identificacao: ident() });
    const txid = await txidDe(p.tokenAcesso);
    expect(await processarCobranca(escolaId, txid, "teste")).toBe("ativa");
    SandboxProvider.simularPagamento(txid);
    const resultados = await Promise.all([1, 2, 3].map(() => processarCobranca(escolaId, txid, "teste")));
    expect(resultados.filter((r) => r === "confirmado")).toHaveLength(1);
    const d = await pedidoPorToken(SLUG, p.tokenAcesso);
    expect(d).toMatchObject({ status: "PAGO", pix: null });
    expect(d!.endToEndId).toMatch(/^E/);
  });

  it("valor pago diferente do cobrado não confirma o pedido", async () => {
    const p = await criarPedido(SLUG, { itens: [{ itemId: cota20 }], identificacao: ident() });
    const txid = await txidDe(p.tokenAcesso);
    SandboxProvider.simularPagamento(txid, 1999);
    expect(await processarCobranca(escolaId, txid, "teste")).toBe("divergente");
    expect(await pedidoPorToken(SLUG, p.tokenAcesso)).toMatchObject({ status: "PIX_GERADO", emAnalise: true });
  });

  it("webhook: segredo errado é recusado e registrado; o correto confirma após reconsulta", async () => {
    const p = await criarPedido(SLUG, { itens: [{ itemId: cota20 }], identificacao: ident() });
    const txid = await txidDe(p.tokenAcesso);
    const segredo = await comEscola(escolaId, async (tx) => (await tx.select().from(credenciaisPix))[0]!.segredoWebhook);
    const corpo = JSON.stringify({ pix: [{ endToEndId: "E123", txid, valor: "20.00", horario: new Date().toISOString() }] });
    const chamar = (s: string) => webhook(new Request(`http://x/api/webhooks/pix/${escolaId}/${s}/pix`, { method: "POST", body: corpo }),
      { params: Promise.resolve({ escolaId, segredo: s }) });

    expect((await chamar("errado")).status).toBe(401);
    // Aviso com segredo certo, mas o banco ainda não registra o pagamento: nada muda.
    expect((await chamar(segredo)).status).toBe(200);
    expect((await pedidoPorToken(SLUG, p.tokenAcesso))!.status).toBe("PIX_GERADO");

    SandboxProvider.simularPagamento(txid);
    expect((await chamar(segredo)).status).toBe(200);
    expect((await pedidoPorToken(SLUG, p.tokenAcesso))!.status).toBe("PAGO");
    const evs = await comEscola(escolaId, (tx) => tx.select().from(eventosWebhook));
    expect(evs.map((e) => e.valido).sort()).toEqual([false, true, true]);
  });

  it("conciliação registra Pix recebido sem pedido como avulso", async () => {
    const falso = new SandboxProvider(escolaId);
    falso.listarPixRecebidos = async () => [{ endToEndId: "EAVULSO1", valorCentavos: 1000, horario: new Date(), pagadorNome: "JOSE" }];
    provedoresInjetados.set(escolaId, falso);
    const r = await conciliarPeriodo(escolaId, new Date(Date.now() - 86400000), new Date());
    expect(r.avulsos).toBe(1);
    expect(await comEscola(escolaId, (tx) => tx.select().from(pixAvulsos))).toHaveLength(1);
    expect((await conciliarPeriodo(escolaId, new Date(Date.now() - 86400000), new Date())).avulsos).toBe(0);
  });
});

describe("multi-tenant (Row Level Security)", () => {
  it("uma escola não enxerga nem grava dados de outra", async () => {
    await criarPedido(SLUG, { itens: [{ itemId: cota20 }], identificacao: ident() });
    const [outra] = await comoSistema((tx) => tx.insert(escolas).values({ slug: "outra", nome: "Outra", nomeApm: "APM",
      cnpjApm: "0", camposFormulario: CAMPOS_PADRAO }).returning());

    const vistos = await comEscola(outra!.id, async (tx) => ({
      pedidos: await tx.select().from(pedidos), itens: await tx.select().from(itens),
      credenciais: await tx.select().from(credenciaisPix),
    }));
    expect(vistos).toEqual({ pedidos: [], itens: [], credenciais: [] });

    await expect(comEscola(outra!.id, (tx) => tx.insert(turmas).values({ escolaId, nome: "invasão" })))
      .rejects.toThrow();
    const alterados = await comEscola(outra!.id, (tx) => tx.update(itens).set({ precoCentavos: 1 }).returning());
    expect(alterados).toHaveLength(0);
  });
});
