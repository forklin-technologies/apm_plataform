/**
 * Webhook Pix de uma escola.
 * URL registrada no banco: {APP_URL}/api/webhooks/pix/{escolaId}/{segredo}
 * (o padrão Bacen acrescenta "/pix" ao final — aceito pelo [[...resto]]).
 *
 * Camadas de proteção:
 * 1. mTLS no proxy reverso (só o banco consegue conectar) — ver docs/IMPLANTACAO.md
 * 2. Segredo aleatório por escola na URL, comparado em tempo constante
 * 3. O aviso NÃO confirma nada: processarCobranca reconsulta a API do banco
 * 4. Todo aviso, válido ou não, fica registrado em eventos_webhook
 */
import { NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { comEscola } from "@/db/cliente";
import { credenciaisPix, eventosWebhook } from "@/db/schema";
import { iguaisSeguro } from "@/lib/cripto";
import { ipDaRequisicao } from "@/lib/limite";
import { log } from "@/lib/log";
import { provedorDaEscola } from "@/pix/fabrica";
import { processarCobranca } from "@/services/confirmacao";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function POST(req: Request, { params }: { params: Promise<{ escolaId: string; segredo: string }> }) {
  const { escolaId, segredo } = await params;
  if (!UUID.test(escolaId)) return new NextResponse(null, { status: 404 });
  const ip = ipDaRequisicao(req);
  const texto = (await req.text()).slice(0, 256_000);
  let corpo: unknown = null;
  try { corpo = JSON.parse(texto); } catch { /* registra mesmo inválido */ }

  const { valido, eventoId, notificacoes } = await comEscola(escolaId, async (tx) => {
    const [cred] = await tx.select().from(credenciaisPix).where(eq(credenciaisPix.escolaId, escolaId));
    const ok = !!cred && iguaisSeguro(segredo, cred.segredoWebhook);
    const [ev] = await tx.insert(eventosWebhook).values({
      escolaId: cred ? escolaId : null, provedor: cred?.provedor ?? "desconhecido", ip, corpo, valido: ok,
    }).returning({ id: eventosWebhook.id });
    const notifs = ok ? (await provedorDaEscola(tx, escolaId)).interpretarWebhook(corpo) : [];
    return { valido: ok, eventoId: ev!.id, notificacoes: notifs };
  });

  if (!valido) {
    log.warn({ escolaId, ip }, "webhook com segredo inválido");
    return new NextResponse(null, { status: 401 });
  }

  const erros: string[] = [];
  for (const n of notificacoes) {
    if (!n.txid) continue; // Pix sem cobrança (direto na chave): tratado pela conciliação
    try { await processarCobranca(escolaId, n.txid, "webhook"); }
    catch (e) { erros.push(`${n.txid}: ${e instanceof Error ? e.message : "erro"}`); log.error({ err: e, txid: n.txid }, "falha ao processar webhook"); }
  }
  await comEscola(escolaId, (tx) => tx.update(eventosWebhook)
    .set({ processadoEm: new Date(), erro: erros.length ? erros.join("; ").slice(0, 2000) : null })
    .where(eq(eventosWebhook.id, eventoId)));

  // Erro de processamento devolve 500 para o banco reenviar; a rotina também cobre.
  return new NextResponse(null, { status: erros.length ? 500 : 200 });
}
