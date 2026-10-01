/**
 * Consultas públicas: vitrine da escola e situação de um pedido pelo link.
 * Retornam SÓ o necessário para a tela — nada de dados de outras famílias.
 */
import { and, asc, eq } from "drizzle-orm";
import { comEscola, db } from "@/db/cliente";
import { campanhas, escolas, itens, itensPedido, pagamentos, pedidos, turmas } from "@/db/schema";

export async function escolaPorSlug(slug: string) {
  const [e] = await db().select().from(escolas).where(and(eq(escolas.slug, slug), eq(escolas.ativa, true)));
  return e ?? null;
}

/** Escolas ativas, para a página inicial escolher a escola. */
export async function escolasAtivas() {
  return db().select({ slug: escolas.slug, nome: escolas.nome, nomeApm: escolas.nomeApm })
    .from(escolas).where(eq(escolas.ativa, true)).orderBy(asc(escolas.nome));
}

export async function vitrine(slug: string, agora = new Date()) {
  const escola = await escolaPorSlug(slug);
  if (!escola) return null;
  return comEscola(escola.id, async (tx) => {
    const cs = await tx.select().from(campanhas).where(eq(campanhas.status, "ATIVA")).orderBy(asc(campanhas.ordem));
    const ativas = cs.filter((c) => c.dataInicio <= agora && (!c.dataFim || c.dataFim >= agora));
    const is = await tx.select().from(itens).where(eq(itens.ativo, true)).orderBy(asc(itens.ordem));
    const ts = await tx.select({ id: turmas.id, nome: turmas.nome }).from(turmas)
      .where(eq(turmas.ativa, true)).orderBy(asc(turmas.ordem), asc(turmas.nome));
    const noPeriodo = (i: typeof is[number]) => (!i.inicioVenda || i.inicioVenda <= agora) && (!i.fimVenda || i.fimVenda >= agora);
    return {
      escola: {
        slug: escola.slug, nome: escola.nome, nomeApm: escola.nomeApm, logoUrl: escola.logoUrl,
        corPrimaria: escola.corPrimaria, corSecundaria: escola.corSecundaria,
        mensagemInicial: escola.mensagemInicial, mensagemAgradecimento: escola.mensagemAgradecimento,
        modoIdentificacao: escola.modoIdentificacao, camposFormulario: escola.camposFormulario,
        valorMinimoLivreCentavos: escola.valorMinimoLivreCentavos,
      },
      turmas: ts,
      campanhas: ativas.map((c) => ({
        id: c.id, slug: c.slug, nome: c.nome, descricao: c.descricao, imagemUrl: c.imagemUrl,
        textoApresentacao: c.textoApresentacao, metaCentavos: c.metaCentavos,
        itens: is.filter((i) => i.campanhaId === c.id && noPeriodo(i)).map((i) => ({
          id: i.id, tipo: i.tipo, nome: i.nome, descricao: i.descricao, imagemUrl: i.imagemUrl,
          precoCentavos: i.precoCentavos, limitePorPedido: i.limitePorPedido,
          disponivel: i.controlaEstoque ? Math.max(0, i.estoqueTotal! - i.estoqueReservado - i.estoqueVendido) : null,
        })),
      })),
    };
  });
}

export async function pedidoPorToken(slug: string, token: string) {
  const escola = await escolaPorSlug(slug);
  if (!escola || !/^[A-Za-z0-9_-]{20,64}$/.test(token)) return null;
  return comEscola(escola.id, async (tx) => {
    const [p] = await tx.select().from(pedidos).where(eq(pedidos.tokenAcesso, token));
    if (!p) return null;
    const linhas = await tx.select({ descricao: itensPedido.descricao, quantidade: itensPedido.quantidade,
      valorTotalCentavos: itensPedido.valorTotalCentavos }).from(itensPedido).where(eq(itensPedido.pedidoId, p.id));
    const [pag] = await tx.select({ txid: pagamentos.txid, pixCopiaECola: pagamentos.pixCopiaECola,
      endToEndId: pagamentos.endToEndId, status: pagamentos.status })
      .from(pagamentos).where(eq(pagamentos.pedidoId, p.id)).orderBy(asc(pagamentos.criadoEm));
    return {
      escola: { nome: escola.nome, nomeApm: escola.nomeApm, cnpjApm: escola.cnpjApm, logoUrl: escola.logoUrl,
                mensagemAgradecimento: escola.mensagemAgradecimento, corPrimaria: escola.corPrimaria },
      codigo: p.codigo, status: p.status, emAnalise: p.requerRevisao, totalCentavos: p.totalCentavos,
      responsavelNome: p.anonimo ? null : p.responsavelNome, criadoEm: p.criadoEm, pagoEm: p.pagoEm, expiraEm: p.expiraEm,
      itens: linhas,
      pix: pag && (p.status === "PIX_GERADO") ? { copiaECola: pag.pixCopiaECola } : null,
      txid: pag?.txid ?? null,
      endToEndId: p.status === "PAGO" ? pag?.endToEndId ?? null : null,
    };
  });
}
