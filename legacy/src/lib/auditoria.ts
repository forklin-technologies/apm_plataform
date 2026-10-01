import type { Tx } from "@/db/cliente";
import { logsAuditoria } from "@/db/schema";

export async function auditar(tx: Tx, e: {
  escolaId: string | null; usuarioId?: string | null; acao: string; entidade: string; entidadeId: string;
  antes?: unknown; depois?: unknown; ip?: string | null;
}) {
  await tx.insert(logsAuditoria).values({
    escolaId: e.escolaId, usuarioId: e.usuarioId ?? null, acao: e.acao, entidade: e.entidade,
    entidadeId: e.entidadeId, antes: e.antes ?? null, depois: e.depois ?? null, ip: e.ip ?? null,
  });
}
