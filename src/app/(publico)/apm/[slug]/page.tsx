/** Página pública da escola: campanhas, carrinho e identificação. */
import { cache } from "react";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { vitrine as consultarVitrine } from "@/services/consultas";
import { Moldura } from "../../_componentes/Moldura";
import { Contribuir } from "./Contribuir";

export const dynamic = "force-dynamic";

const vitrine = cache((slug: string) => consultarVitrine(slug));

type Params = { params: Promise<{ slug: string }> };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const v = await vitrine((await params).slug);
  return { title: v ? `Contribua com a APM – ${v.escola.nome}` : "Página não encontrada" };
}

export default async function PaginaEscola({ params }: Params) {
  const { slug } = await params;
  const v = await vitrine(slug);
  if (!v) notFound();

  return (
    <Moldura escola={v.escola}>
      <h1>Contribua com a APM</h1>
      {v.escola.mensagemInicial && <p className="suave">{v.escola.mensagemInicial}</p>}
      {v.campanhas.every((c) => c.itens.length === 0) ? (
        <div className="cartao">
          <p>Não há campanhas abertas no momento. Volte mais tarde ou fale com a APM.</p>
        </div>
      ) : (
        <Contribuir slug={slug} escola={v.escola} turmas={v.turmas}
          campanhas={v.campanhas.filter((c) => c.itens.length > 0)} anoAtual={new Date().getFullYear()} />
      )}
    </Moldura>
  );
}
