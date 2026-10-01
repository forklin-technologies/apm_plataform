/**
 * Criação de pedidos.
 *
 * REGRA DE OURO: o navegador informa apenas QUAIS itens e QUANTOS.
 * Preços, total e valor da cobrança são sempre calculados aqui, a partir do banco.
 */
import { and, eq, inArray, sql } from "drizzle-orm";
import { z } from "zod";
import { comEscola, db, type Tx } from "@/db/cliente";
import { campanhas, escolas, itens, itensPedido, pagamentos, pedidos, turmas, type CamposFormulario } from "@/db/schema";
import { formatarCodigoPedido, gerarTokenAcesso, gerarTxid } from "@/lib/ids";
import { ErroNegocio, ErroProvedorPix } from "@/lib/erros";
import { auditar } from "@/lib/auditoria";
import { log } from "@/lib/log";
import { provedorDaEscola } from "@/pix/fabrica";
import { liberarReserva, reservar } from "./estoque";

const MES = /^20\d{2}-(0[1-9]|1[0-2])$/;
const texto = (max: number) => z.string().trim().max(max).optional().transform((v) => (v ? v : undefined));

export const entradaPedidoSchema = z.object({
  itens: z.array(z.object({
    itemId: z.uuid(),
    quantidade: z.number().int().min(1).max(50).default(1),
    valorCentavos: z.number().int().positive().optional(),           // só VALOR_LIVRE
    meses: z.array(z.string().regex(MES)).max(12).optional(),         // só CONTRIBUICAO_MENSAL
  })).min(1).max(30),
  identificacao: z.object({
    responsavelNome: texto(120),
    responsavelEmail: z.email().max(160).optional().or(z.literal("").transform(() => undefined)),
    responsavelTelefone: z.string().trim().regex(/^[\d\s()+-]{8,20}$/).optional().or(z.literal("").transform(() => undefined)),
    alunoNome: texto(120),
    turmaId: z.uuid().optional().or(z.literal("").transform(() => undefined)),
    observacao: texto(500),
  }).prefault({}),
  anonimo: z.boolean().default(false),
});
export type EntradaPedido = z.input<typeof entradaPedidoSchema>;

export interface PedidoCriado {
  codigo: string;
  tokenAcesso: string;
  totalCentavos: number;
  pixCopiaECola: string;
  expiraEm: Date;
}

type Linha = {
  itemId: string; campanhaId: string; tipo: typeof itens.$inferSelect["tipo"]; descricao: string;
  mesReferencia: string | null; quantidade: number; unit: number; total: number;
};

const NOMES_MES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
export const descreverMes = (m: string) => `${NOMES_MES[Number(m.slice(5, 7)) - 1]}/${m.slice(0, 4)}`;

function validarIdentificacao(
  id: z.infer<typeof entradaPedidoSchema>["identificacao"], campos: CamposFormulario,
  modo: typeof escolas.$inferSelect["modoIdentificacao"], anonimo: boolean, temProduto: boolean,
) {
  if (anonimo) {
    if (modo !== "ANONIMA") throw new ErroNegocio("ANONIMO_NAO_PERMITIDO", "Esta escola não aceita contribuições anônimas.");
    if (temProduto) throw new ErroNegocio("ANONIMO_COM_PRODUTO", "Pedidos com produtos precisam de identificação para a entrega.");
    return {}; // nenhum dado pessoal é guardado
  }
  const saida: Record<string, string | undefined> = {};
  for (const [campo, regra] of Object.entries(campos) as [keyof CamposFormulario, string][]) {
    const chave = campo === "turma" ? "turmaId" : campo;
    const valor = id[chave as keyof typeof id];
    if (regra === "oculto") continue;                         // campo desligado: descarta o que vier
    const obrigatorio = regra === "obrigatorio" && modo === "IDENTIFICADA";
    if (obrigatorio && !valor) throw new ErroNegocio("CAMPO_OBRIGATORIO", `Preencha o campo obrigatório: ${rotulo(campo)}.`);
    saida[chave] = valor;
  }
  if (temProduto && !saida.responsavelNome && !saida.alunoNome) {
    throw new ErroNegocio("IDENTIFICACAO_ENTREGA", "Informe o nome do responsável ou do estudante para a entrega do produto.");
  }
  return saida;
}

function rotulo(c: keyof CamposFormulario) {
  return ({ responsavelNome: "nome do responsável", responsavelEmail: "e-mail", responsavelTelefone: "telefone",
            alunoNome: "nome do estudante", turma: "turma", observacao: "observação" })[c];
}

async function montarLinhas(tx: Tx, entrada: z.infer<typeof entradaPedidoSchema>, escola: typeof escolas.$inferSelect, agora: Date) {
  const ids = [...new Set(entrada.itens.map((i) => i.itemId))];
  const encontrados = await tx.select({ item: itens, campanha: campanhas })
    .from(itens).innerJoin(campanhas, eq(campanhas.id, itens.campanhaId))
    .where(inArray(itens.id, ids));
  const porId = new Map(encontrados.map((e) => [e.item.id, e]));

  const linhas: Linha[] = [];
  const qtdPorItem = new Map<string, number>();
  const mesesPorItem = new Map<string, Set<string>>();

  for (const pedido of entrada.itens) {
    const e = porId.get(pedido.itemId);
    const indisponivel = () => new ErroNegocio("ITEM_INDISPONIVEL", "Um dos itens escolhidos não está mais disponível. Atualize a página.");
    if (!e) throw indisponivel();
    const { item, campanha } = e;
    const vendendo = item.ativo && campanha.status === "ATIVA"
      && campanha.dataInicio <= agora && (!campanha.dataFim || campanha.dataFim >= agora)
      && (!item.inicioVenda || item.inicioVenda <= agora) && (!item.fimVenda || item.fimVenda >= agora);
    if (!vendendo) throw indisponivel();

    const base = { itemId: item.id, campanhaId: campanha.id, tipo: item.tipo };
    switch (item.tipo) {
      case "COTA":
      case "PRODUTO": {
        if (pedido.valorCentavos !== undefined || pedido.meses) throw new ErroNegocio("ENTRADA_INVALIDA", "Dados do item inválidos.");
        const unit = item.precoCentavos!;
        linhas.push({ ...base, descricao: item.nome, mesReferencia: null, quantidade: pedido.quantidade, unit, total: unit * pedido.quantidade });
        qtdPorItem.set(item.id, (qtdPorItem.get(item.id) ?? 0) + pedido.quantidade);
        break;
      }
      case "VALOR_LIVRE": {
        const v = pedido.valorCentavos;
        const minimo = Math.max(escola.valorMinimoLivreCentavos, campanha.valorMinimoCentavos ?? 0);
        if (v === undefined) throw new ErroNegocio("VALOR_OBRIGATORIO", "Informe o valor da contribuição.");
        if (v < minimo) throw new ErroNegocio("VALOR_MINIMO", `O valor mínimo é de R$ ${(minimo / 100).toFixed(2).replace(".", ",")}.`);
        if (v > escola.valorMaximoLivreCentavos) throw new ErroNegocio("VALOR_MAXIMO", "Valor acima do limite aceito pela página. Fale com a APM.");
        if (pedido.quantidade !== 1) throw new ErroNegocio("ENTRADA_INVALIDA", "Dados do item inválidos.");
        linhas.push({ ...base, descricao: item.nome, mesReferencia: null, quantidade: 1, unit: v, total: v });
        break;
      }
      case "CONTRIBUICAO_MENSAL": {
        const meses = pedido.meses ?? [];
        if (meses.length === 0) throw new ErroNegocio("MESES_OBRIGATORIOS", "Escolha o(s) mês(es) da contribuição.");
        const vistos = mesesPorItem.get(item.id) ?? new Set<string>();
        for (const m of meses) {
          if (vistos.has(m)) throw new ErroNegocio("MES_REPETIDO", `O mês ${descreverMes(m)} foi escolhido mais de uma vez.`);
          vistos.add(m);
          const unit = item.precoCentavos!;
          linhas.push({ ...base, descricao: `${item.nome} – ${descreverMes(m)}`, mesReferencia: m, quantidade: 1, unit, total: unit });
        }
        mesesPorItem.set(item.id, vistos);
        break;
      }
    }
  }

  for (const [itemId, qtd] of qtdPorItem) {
    const { item } = porId.get(itemId)!;
    if (item.limitePorPedido && qtd > item.limitePorPedido) {
      throw new ErroNegocio("LIMITE_POR_PEDIDO", `"${item.nome}" permite no máximo ${item.limitePorPedido} por pedido.`);
    }
  }
  return { linhas, qtdPorItem, porId };
}

export async function criarPedido(slug: string, bruto: unknown, ctx: { ip?: string } = {}): Promise<PedidoCriado> {
  const parsed = entradaPedidoSchema.safeParse(bruto);
  if (!parsed.success) throw new ErroNegocio("ENTRADA_INVALIDA", "Confira os dados informados.", 400);
  const entrada = parsed.data;

  const [escola] = await db().select().from(escolas).where(and(eq(escolas.slug, slug), eq(escolas.ativa, true)));
  if (!escola) throw new ErroNegocio("ESCOLA_NAO_ENCONTRADA", "Página não encontrada.", 404);
  const agora = new Date();

  // Transação 1: valida, reserva estoque e registra o pedido.
  const t1 = await comEscola(escola.id, async (tx) => {
    const provedor = await provedorDaEscola(tx, escola.id); // falha cedo se o Pix não estiver configurado
    const { linhas, qtdPorItem, porId } = await montarLinhas(tx, entrada, escola, agora);
    const temProduto = linhas.some((l) => l.tipo === "PRODUTO");
    const ident = validarIdentificacao(entrada.identificacao, escola.camposFormulario, escola.modoIdentificacao, entrada.anonimo, temProduto);

    if (ident.turmaId) {
      const [t] = await tx.select({ id: turmas.id }).from(turmas).where(and(eq(turmas.id, ident.turmaId), eq(turmas.ativa, true)));
      if (!t) throw new ErroNegocio("TURMA_INVALIDA", "Turma inválida.");
    }

    const total = linhas.reduce((s, l) => s + l.total, 0);
    if (total <= 0) throw new ErroNegocio("TOTAL_INVALIDO", "O pedido está vazio.");

    for (const [itemId, qtd] of qtdPorItem) {
      const { item } = porId.get(itemId)!;
      if (item.controlaEstoque) await reservar(tx, itemId, qtd, item.nome);
    }

    const [num] = await tx.update(escolas)
      .set({ proximoNumeroPedido: sql`${escolas.proximoNumeroPedido} + 1` })
      .where(eq(escolas.id, escola.id))
      .returning({ n: sql<number>`${escolas.proximoNumeroPedido} - 1` });
    const codigo = formatarCodigoPedido(num!.n);
    const expiraEm = new Date(agora.getTime() + escola.expiracaoPixSegundos * 1000);

    const [ped] = await tx.insert(pedidos).values({
      escolaId: escola.id, codigo, tokenAcesso: gerarTokenAcesso(),
      responsavelNome: ident.responsavelNome, responsavelEmail: ident.responsavelEmail,
      responsavelTelefone: ident.responsavelTelefone, alunoNome: ident.alunoNome,
      turmaId: ident.turmaId, observacao: ident.observacao, anonimo: entrada.anonimo,
      totalCentavos: total, statusEntrega: temProduto ? "PENDENTE" : "NAO_SE_APLICA", expiraEm,
    }).returning();

    await tx.insert(itensPedido).values(linhas.map((l) => ({
      escolaId: escola.id, pedidoId: ped!.id, itemId: l.itemId, campanhaId: l.campanhaId, tipo: l.tipo,
      descricao: l.descricao, mesReferencia: l.mesReferencia, quantidade: l.quantidade,
      valorUnitarioCentavos: l.unit, valorTotalCentavos: l.total,
    })));

    const txid = gerarTxid();
    await tx.insert(pagamentos).values({
      escolaId: escola.id, pedidoId: ped!.id, provedor: provedor.nome, txid, valorCentavos: total, expiraEm,
    });
    await auditar(tx, { escolaId: escola.id, acao: "pedido.criado", entidade: "pedido", entidadeId: ped!.id,
      depois: { codigo, total, itens: linhas.length }, ip: ctx.ip });
    return { pedido: ped!, txid, provedor };
  });

  // Chamada ao banco FORA da transação (não segura bloqueios durante a rede).
  try {
    const cob = await t1.provedor.criarCobranca({
      txid: t1.txid, valorCentavos: t1.pedido.totalCentavos, expiracaoSegundos: escola.expiracaoPixSegundos,
      solicitacaoPagador: `${escola.nomeApm} – pedido ${t1.pedido.codigo}`,
      infoAdicionais: [{ nome: "Pedido", valor: t1.pedido.codigo }],
    });
    await comEscola(escola.id, async (tx) => {
      await tx.update(pagamentos).set({ status: "ATIVO", pixCopiaECola: cob.pixCopiaECola }).where(eq(pagamentos.txid, t1.txid));
      await tx.update(pedidos).set({ status: "PIX_GERADO" })
        .where(and(eq(pedidos.id, t1.pedido.id), eq(pedidos.status, "AGUARDANDO_PAGAMENTO")));
    });
    return { codigo: t1.pedido.codigo, tokenAcesso: t1.pedido.tokenAcesso, totalCentavos: t1.pedido.totalCentavos,
             pixCopiaECola: cob.pixCopiaECola, expiraEm: t1.pedido.expiraEm };
  } catch (e) {
    log.error({ err: e, pedido: t1.pedido.codigo }, "falha ao criar cobrança Pix");
    await comEscola(escola.id, async (tx) => {
      await tx.update(pagamentos).set({ status: "FALHOU", erro: e instanceof Error ? e.message.slice(0, 500) : "erro" })
        .where(eq(pagamentos.txid, t1.txid));
      await tx.update(pedidos).set({ status: "CANCELADO" }).where(eq(pedidos.id, t1.pedido.id));
      await liberarReserva(tx, t1.pedido.id);
    });
    if (e instanceof ErroProvedorPix) {
      throw new ErroNegocio("PIX_INDISPONIVEL", "Não foi possível gerar o Pix agora. Tente novamente em alguns minutos.", 503);
    }
    throw e;
  }
}
