/**
 * GET: situação do pedido (a tela do Pix consulta a cada poucos segundos).
 * Se o pedido ainda aguarda pagamento, no máximo a cada 15 s reconsulta o banco
 * — assim a confirmação aparece mesmo se o webhook atrasar.
 */
import { NextResponse } from "next/server";
import { pedidoPorToken, escolaPorSlug } from "@/services/consultas";
import { processarCobranca } from "@/services/confirmacao";
import { ipDaRequisicao, permitir } from "@/lib/limite";
import { log } from "@/lib/log";

export async function GET(req: Request, { params }: { params: Promise<{ slug: string; token: string }> }) {
  const { slug, token } = await params;
  if (!permitir(`consulta:${ipDaRequisicao(req)}`, 120, 60_000)) return NextResponse.json({ erro: "MUITAS_TENTATIVAS" }, { status: 429 });
  let p = await pedidoPorToken(slug, token);
  if (!p) return NextResponse.json({ erro: "NAO_ENCONTRADO" }, { status: 404 });

  if (p.status === "PIX_GERADO" && p.txid && permitir(`reconsulta:${p.txid}`, 1, 15_000)) {
    const escola = await escolaPorSlug(slug);
    try {
      if (escola && (await processarCobranca(escola.id, p.txid, "tela")) !== "ativa") p = await pedidoPorToken(slug, token);
    } catch (e) { log.warn({ err: e }, "reconsulta falhou; seguirá pelo webhook/rotina"); }
  }
  const { txid: _omit, ...publico } = p!;
  return NextResponse.json(publico, { headers: { "Cache-Control": "no-store" } });
}
