import Link from "next/link";
import { escolasAtivas } from "@/services/consultas";
import { log } from "@/lib/log";

export const dynamic = "force-dynamic";

async function carregar() {
  try { return await escolasAtivas(); } catch (e) { log.error({ err: e }, "falha ao listar escolas"); return null; }
}

export default async function Inicio() {
  const escolas = await carregar();
  return (
    <>
      <div className="faixa" aria-hidden="true" />
      <main id="conteudo" className="conteudo" tabIndex={-1}>
        <h1>Plataforma APM</h1>
        <p className="suave">Contribuições, campanhas e vendas da APM da sua escola, pagas por Pix.</p>

        {escolas === null ? (
          <div className="alerta erro" role="alert">
            <p>Não foi possível carregar as escolas agora. Tente novamente em instantes.</p>
          </div>
        ) : escolas.length === 0 ? (
          <p>Nenhuma escola disponível no momento. Acesse o link enviado pela sua escola.</p>
        ) : (
          <nav aria-labelledby="titulo-escolas">
            <h2 id="titulo-escolas">Escolha a sua escola</h2>
            <ul className="escolas">
              {escolas.map((e) => (
                <li key={e.slug}>
                  <Link href={`/apm/${e.slug}`}>
                    {e.nome}
                    <span className="suave">{e.nomeApm}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        )}
      </main>
    </>
  );
}
