/** GET: QR Code (SVG) do Pix do pedido. */
import { pedidoPorToken } from "@/services/consultas";
import { qrCodeSvg } from "@/pix/qrcode";

export async function GET(_req: Request, { params }: { params: Promise<{ slug: string; token: string }> }) {
  const { slug, token } = await params;
  const p = await pedidoPorToken(slug, token);
  if (!p?.pix?.copiaECola) return new Response("Não encontrado", { status: 404 });
  return new Response(await qrCodeSvg(p.pix.copiaECola), {
    headers: { "Content-Type": "image/svg+xml", "Cache-Control": "private, no-store" },
  });
}
