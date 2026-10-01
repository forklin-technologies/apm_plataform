/**
 * Controle de estoque com reserva.
 *
 * disponível = estoque_total - estoque_reservado - estoque_vendido
 *
 * A reserva é UMA instrução atômica com a condição de disponibilidade no WHERE:
 * dois pedidos disputando o último item nunca conseguem reservar ambos.
 */
import { and, eq, sql } from "drizzle-orm";
import type { Tx } from "@/db/cliente";
import { itens, itensPedido, pedidos } from "@/db/schema";
import { ErroNegocio } from "@/lib/erros";

export async function reservar(tx: Tx, itemId: string, quantidade: number, nomeItem: string) {
  const r = await tx.update(itens)
    .set({ estoqueReservado: sql`${itens.estoqueReservado} + ${quantidade}` })
    .where(and(
      eq(itens.id, itemId),
      sql`${itens.estoqueTotal} - ${itens.estoqueReservado} - ${itens.estoqueVendido} >= ${quantidade}`,
    ))
    .returning({ id: itens.id });
  if (r.length === 0) throw new ErroNegocio("ESGOTADO", `"${nomeItem}" não tem quantidade disponível suficiente.`, 409);
}

/** Quantidades por item com controle de estoque em um pedido. */
async function quantidadesControladas(tx: Tx, pedidoId: string) {
  return tx.select({ itemId: itensPedido.itemId, qtd: sql<number>`sum(${itensPedido.quantidade})::int` })
    .from(itensPedido)
    .innerJoin(itens, eq(itens.id, itensPedido.itemId))
    .where(and(eq(itensPedido.pedidoId, pedidoId), eq(itens.controlaEstoque, true)))
    .groupBy(itensPedido.itemId);
}

/** Devolve a reserva ao estoque (expiração/cancelamento). Idempotente. */
export async function liberarReserva(tx: Tx, pedidoId: string) {
  const marcado = await tx.update(pedidos).set({ estoqueLiberado: true })
    .where(and(eq(pedidos.id, pedidoId), eq(pedidos.estoqueLiberado, false)))
    .returning({ id: pedidos.id });
  if (marcado.length === 0) return;
  for (const { itemId, qtd } of await quantidadesControladas(tx, pedidoId)) {
    await tx.update(itens).set({ estoqueReservado: sql`${itens.estoqueReservado} - ${qtd}` }).where(eq(itens.id, itemId));
  }
}

/**
 * Converte a reserva em venda (pagamento confirmado).
 * Se a reserva já tinha sido liberada (pagamento chegou após expirar),
 * baixa direto do estoque e informa se ficou negativo para revisão.
 */
export async function confirmarVenda(tx: Tx, pedidoId: string, reservaLiberada: boolean): Promise<string[]> {
  const semEstoque: string[] = [];
  for (const { itemId, qtd } of await quantidadesControladas(tx, pedidoId)) {
    const [r] = await tx.update(itens).set({
      estoqueVendido: sql`${itens.estoqueVendido} + ${qtd}`,
      ...(reservaLiberada ? {} : { estoqueReservado: sql`${itens.estoqueReservado} - ${qtd}` }),
    }).where(eq(itens.id, itemId))
      .returning({ nome: itens.nome, total: itens.estoqueTotal, reservado: itens.estoqueReservado, vendido: itens.estoqueVendido });
    if (r && r.total !== null && r.vendido + r.reservado > r.total) semEstoque.push(r.nome);
  }
  return semEstoque;
}
