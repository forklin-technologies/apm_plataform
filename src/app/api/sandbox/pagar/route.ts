/**
 * SOMENTE DESENVOLVIMENTO: simula que a família pagou o Pix do sandbox e
 * dispara o mesmo fluxo de confirmação usado com bancos reais.
 * Em produção esta rota responde 404.
 */
import { NextResponse } from "next/server";
import { z } from "zod";
import { SandboxProvider, sandboxPermitido } from "@/pix/sandbox";
import { escolaPorSlug } from "@/services/consultas";
import { processarCobranca } from "@/services/confirmacao";
import { comEscola } from "@/db/cliente";
import { pagamentos, pedidos } from "@/db/schema";
import { eq } from "drizzle-orm";

export async function POST(req: Request) {
  if (!sandboxPermitido()) return new NextResponse(null, { status: 404 });
  const { slug, token } = z.object({ slug: z.string(), token: z.string() }).parse(await req.json());
  const escola = await escolaPorSlug(slug);
  if (!escola) return new NextResponse(null, { status: 404 });
  const txid = await comEscola(escola.id, async (tx) => {
    const [r] = await tx.select({ txid: pagamentos.txid }).from(pagamentos)
      .innerJoin(pedidos, eq(pedidos.id, pagamentos.pedidoId)).where(eq(pedidos.tokenAcesso, token));
    return r?.txid;
  });
  if (!txid) return new NextResponse(null, { status: 404 });
  SandboxProvider.simularPagamento(txid);
  return NextResponse.json({ resultado: await processarCobranca(escola.id, txid, "sandbox") });
}
