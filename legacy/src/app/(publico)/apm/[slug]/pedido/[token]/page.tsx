/** Tela do Pix e comprovante, acessada pelo link secreto do pedido. */
import { cache } from "react";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { pedidoPorToken } from "@/services/consultas";
import { sandboxPermitido } from "@/pix/sandbox";
import { Moldura } from "../../../../_componentes/Moldura";
import { TelaPedido, type PedidoPublico } from "./TelaPedido";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ slug: string; token: string }> };

const carregar = cache(async (slug: string, token: string) => {
  const p = await pedidoPorToken(slug, token);
  if (!p) return null;
  const { txid: _omit, escola, ...resto } = p;
  // Datas viram texto: é o mesmo formato que a consulta periódica recebe da API.
  return { escola, pedido: JSON.parse(JSON.stringify(resto)) as PedidoPublico };
});

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { slug, token } = await params;
  const r = await carregar(slug, token);
  return {
    title: r ? `Pedido ${r.pedido.codigo} – ${r.escola.nome}` : "Pedido não encontrado",
    robots: { index: false, follow: false },
    referrer: "no-referrer",
  };
}

export default async function PaginaPedido({ params }: Params) {
  const { slug, token } = await params;
  const r = await carregar(slug, token);
  if (!r) notFound();
  return (
    <Moldura escola={r.escola}>
      <TelaPedido slug={slug} token={token} inicial={r.pedido}
        escola={{ nome: r.escola.nome, nomeApm: r.escola.nomeApm, cnpjApm: r.escola.cnpjApm, mensagemAgradecimento: r.escola.mensagemAgradecimento }}
        sandbox={sandboxPermitido()} />
    </Moldura>
  );
}
