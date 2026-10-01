/** POST: cria um pedido e a cobrança Pix. */
import { NextResponse } from "next/server";
import { criarPedido } from "@/services/pedidos";
import { ipDaRequisicao, permitir } from "@/lib/limite";
import { origemPermitida, responderErro } from "@/lib/http";

export async function POST(req: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const ip = ipDaRequisicao(req);
  if (!origemPermitida(req)) return NextResponse.json({ erro: "ORIGEM" }, { status: 403 });
  if (!permitir(`pedido:${ip}`, 10, 10 * 60_000)) {
    return NextResponse.json({ erro: "MUITAS_TENTATIVAS", mensagem: "Muitas tentativas. Aguarde alguns minutos." }, { status: 429 });
  }
  try {
    const corpo = await req.json().catch(() => null);
    const p = await criarPedido(slug, corpo, { ip });
    return NextResponse.json({ codigo: p.codigo, token: p.tokenAcesso, totalCentavos: p.totalCentavos,
      pixCopiaECola: p.pixCopiaECola, expiraEm: p.expiraEm.toISOString() }, { status: 201 });
  } catch (e) {
    return responderErro(e);
  }
}
