/**
 * Rotinas periódicas (executadas por scripts/jobs.ts):
 * - verificarPendentes: reconsulta cobranças ativas (rede de segurança do webhook)
 *   e expira pedidos vencidos, liberando o estoque reservado.
 * - conciliarPeriodo: compara todos os Pix recebidos na chave com os pedidos;
 *   Pix sem pedido vão para a fila de "Pix avulsos".
 */
import { eq, inArray } from "drizzle-orm";
import { comEscola, comoSistema } from "@/db/cliente";
import { escolas, pagamentos, pedidos, pixAvulsos } from "@/db/schema";
import { log } from "@/lib/log";
import { provedorDaEscola } from "@/pix/fabrica";
import { expirarNaTransacao, processarCobranca } from "./confirmacao";

/** Margem após o vencimento antes de expirar localmente (atrasos de rede/relógio). */
const MARGEM_MS = 2 * 60 * 1000;

export async function verificarPendentes(agora = new Date()) {
  const pendentes = await comoSistema((tx) => tx.select({
    escolaId: pagamentos.escolaId, txid: pagamentos.txid, pedidoId: pagamentos.pedidoId,
    pagamentoId: pagamentos.id, expiraEm: pagamentos.expiraEm, status: pagamentos.status,
  }).from(pagamentos).where(inArray(pagamentos.status, ["CRIADO", "ATIVO"])).limit(500));

  const resumo = { confirmados: 0, expirados: 0, erros: 0 };
  for (const p of pendentes) {
    try {
      const vencido = p.expiraEm.getTime() + MARGEM_MS < agora.getTime();
      // CRIADO = a cobrança pode nem ter chegado ao banco (queda entre gravar e chamar o banco).
      if (p.status === "CRIADO") {
        if (!vencido) continue;
        const r = await processarCobranca(p.escolaId, p.txid, "rotina").catch(() => "ativa" as const);
        if (r === "confirmado") { resumo.confirmados++; continue; }
        await comEscola(p.escolaId, (tx) => expirarNaTransacao(tx, p.pedidoId, p.pagamentoId, "EXPIRADO"));
        resumo.expirados++;
        continue;
      }
      const r = await processarCobranca(p.escolaId, p.txid, "rotina");
      if (r === "confirmado") resumo.confirmados++;
      if (r === "expirado") resumo.expirados++;
      // Na API padrão, a cobrança vencida pode continuar "ATIVA", mas o banco não aceita mais pagamento.
      if (r === "ativa" && vencido) {
        await comEscola(p.escolaId, (tx) => expirarNaTransacao(tx, p.pedidoId, p.pagamentoId, "EXPIRADO"));
        resumo.expirados++;
      }
    } catch (e) {
      resumo.erros++;
      log.error({ err: e, txid: p.txid }, "falha ao verificar cobrança pendente");
    }
  }
  return resumo;
}

export async function conciliarPeriodo(escolaId: string, inicio: Date, fim: Date) {
  const provedor = await comEscola(escolaId, (tx) => provedorDaEscola(tx, escolaId));
  const recebidos = await provedor.listarPixRecebidos(inicio, fim);
  const resumo = { recebidos: recebidos.length, confirmados: 0, avulsos: 0 };

  for (const pix of recebidos) {
    const pag = pix.txid
      ? await comEscola(escolaId, async (tx) => (await tx.select().from(pagamentos).where(eq(pagamentos.txid, pix.txid!)))[0])
      : undefined;
    if (pag) {
      if (pag.status !== "CONCLUIDO" && (await processarCobranca(escolaId, pag.txid, "conciliacao")) === "confirmado") resumo.confirmados++;
      continue;
    }
    const r = await comEscola(escolaId, (tx) => tx.insert(pixAvulsos).values({
      escolaId, endToEndId: pix.endToEndId, valorCentavos: pix.valorCentavos, horario: pix.horario,
      infoPagador: [pix.pagadorNome, pix.infoPagador].filter(Boolean).join(" – ") || null,
    }).onConflictDoNothing().returning({ id: pixAvulsos.id }));
    if (r.length) resumo.avulsos++;
  }
  return resumo;
}

export async function conciliarTodasEscolas(inicio: Date, fim: Date) {
  const lista = await comoSistema((tx) => tx.select({ id: escolas.id }).from(escolas).where(eq(escolas.ativa, true)));
  for (const e of lista) {
    try { log.info({ escola: e.id, ...(await conciliarPeriodo(e.id, inicio, fim)) }, "conciliação concluída"); }
    catch (err) { log.error({ err, escola: e.id }, "falha na conciliação"); }
  }
}

