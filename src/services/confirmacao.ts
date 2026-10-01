/**
 * Confirmação de pagamento.
 *
 * Qualquer gatilho (webhook, job de conciliação, consulta da tela do Pix) chama
 * `processarCobranca`. Ela NUNCA confia no gatilho: sempre reconsulta a
 * cobrança na API do banco e só então atualiza o pedido. É idempotente — pode
 * ser chamada várias vezes para o mesmo txid sem duplicar nada.
 */
import { and, eq, inArray } from "drizzle-orm";
import { comEscola } from "@/db/cliente";
import { notificacoes, pagamentos, pedidos } from "@/db/schema";
import { cifrar } from "@/lib/cripto";
import { auditar } from "@/lib/auditoria";
import { log } from "@/lib/log";
import { provedorDaEscola } from "@/pix/fabrica";
import type { ConsultaCobranca } from "@/pix/tipos";
import { confirmarVenda, liberarReserva } from "./estoque";

export type ResultadoProcessamento =
  | "confirmado" | "ja_processado" | "divergente" | "expirado" | "ativa" | "desconhecido";

const PENDENTES = ["AGUARDANDO_PAGAMENTO", "PIX_GERADO"] as const;

export async function processarCobranca(escolaId: string, txid: string, origem: string): Promise<ResultadoProcessamento> {
  const pre = await comEscola(escolaId, async (tx) => {
    const [p] = await tx.select().from(pagamentos).where(eq(pagamentos.txid, txid));
    if (!p) return null;
    return { pagamento: p, provedor: await provedorDaEscola(tx, escolaId) };
  });
  if (!pre) return "desconhecido";
  if (pre.pagamento.status === "CONCLUIDO" || pre.pagamento.status === "DEVOLVIDO") return "ja_processado";

  const consulta: ConsultaCobranca = await pre.provedor.consultarCobranca(txid);
  if (consulta.txid !== txid) throw new Error("Banco devolveu txid diferente do consultado");

  return comEscola(escolaId, async (tx) => {
    // Bloqueia a linha: dois webhooks simultâneos são processados em fila.
    const [pag] = await tx.select().from(pagamentos).where(eq(pagamentos.txid, txid)).for("update");
    if (!pag) return "desconhecido";
    if (pag.status === "CONCLUIDO" || pag.status === "DEVOLVIDO") return "ja_processado";
    const [ped] = await tx.select().from(pedidos).where(eq(pedidos.id, pag.pedidoId)).for("update");
    if (!ped) return "desconhecido";

    if (consulta.status === "CONCLUIDA" && consulta.pagamentos.length > 0) {
      const pago = consulta.pagamentos.reduce((s, p) => s + p.valorCentavos, 0);
      const primeiro = consulta.pagamentos[0]!;
      await tx.update(pagamentos).set({
        status: "CONCLUIDO", endToEndId: primeiro.endToEndId, valorPagoCentavos: pago, pagoEm: primeiro.horario,
        pagadorNomeCifrado: primeiro.pagadorNome ? cifrar(primeiro.pagadorNome) : null,
      }).where(eq(pagamentos.id, pag.id));

      if (pago !== pag.valorCentavos || consulta.valorCentavos !== pag.valorCentavos) {
        await tx.update(pedidos).set({ requerRevisao: true,
          motivoRevisao: `Valor recebido (${pago}) difere do cobrado (${pag.valorCentavos}) — centavos` })
          .where(eq(pedidos.id, ped.id));
        await auditar(tx, { escolaId, acao: "pagamento.divergente", entidade: "pedido", entidadeId: ped.id,
          depois: { pago, cobrado: pag.valorCentavos, origem } });
        log.warn({ pedido: ped.codigo, pago, cobrado: pag.valorCentavos }, "pagamento com valor divergente");
        return "divergente";
      }

      const reservaLiberada = ped.estoqueLiberado;
      const semEstoque = await confirmarVenda(tx, ped.id, reservaLiberada);
      const revisao = [
        ...(ped.status !== "AGUARDANDO_PAGAMENTO" && ped.status !== "PIX_GERADO" ? [`Pago com pedido em ${ped.status}`] : []),
        ...(semEstoque.length ? [`Estoque insuficiente: ${semEstoque.join(", ")}`] : []),
      ];
      await tx.update(pedidos).set({
        status: "PAGO", pagoEm: primeiro.horario, estoqueLiberado: true,
        ...(revisao.length ? { requerRevisao: true, motivoRevisao: revisao.join("; ") } : {}),
      }).where(eq(pedidos.id, ped.id));
      await auditar(tx, { escolaId, acao: "pagamento.confirmado", entidade: "pedido", entidadeId: ped.id,
        depois: { codigo: ped.codigo, valor: pago, endToEndId: primeiro.endToEndId, origem } });
      if (ped.responsavelEmail) {
        await tx.insert(notificacoes).values({ escolaId, evento: "pagamento.confirmado", canal: "email",
          destino: ped.responsavelEmail, payload: { pedidoId: ped.id } });
      }
      return "confirmado";
    }

    if (consulta.status === "EXPIRADA" || consulta.status === "REMOVIDA") {
      await expirarNaTransacao(tx, ped.id, pag.id, consulta.status === "REMOVIDA" ? "REMOVIDO" : "EXPIRADO");
      return "expirado";
    }
    return "ativa";
  });
}

type TxLocal = Parameters<Parameters<typeof comEscola>[1]>[0];

export async function expirarNaTransacao(tx: TxLocal, pedidoId: string, pagamentoId: string, status: "EXPIRADO" | "REMOVIDO") {
  await tx.update(pagamentos).set({ status }).where(and(eq(pagamentos.id, pagamentoId), inArray(pagamentos.status, ["CRIADO", "ATIVO"])));
  const r = await tx.update(pedidos).set({ status: "EXPIRADO" })
    .where(and(eq(pedidos.id, pedidoId), inArray(pedidos.status, [...PENDENTES]))).returning({ id: pedidos.id });
  if (r.length) await liberarReserva(tx, pedidoId);
}
