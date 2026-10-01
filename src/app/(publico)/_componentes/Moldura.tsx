/** Cabeçalho, área principal e rodapé comuns às telas de uma escola. */
import type { CSSProperties, ReactNode } from "react";
import { coresDaEscola } from "@/lib/cores";

interface Props {
  escola: { nome: string; nomeApm: string; logoUrl?: string | null; corPrimaria?: string | null };
  children: ReactNode;
}

export function Moldura({ escola, children }: Props) {
  const cores = coresDaEscola(escola.corPrimaria);
  const estilo = { "--marca": cores.marca, "--marca-escuro": cores.marcaEscuro, "--decorativa": cores.decorativa } as CSSProperties;
  return (
    <div className="tema-escola" style={estilo}>
      <div className="faixa" aria-hidden="true" />
      <header className="cabecalho">
        <div className="conteudo">
          {escola.logoUrl && <img src={escola.logoUrl} alt="" />}
          <div>
            <p className="nome-apm">{escola.nomeApm}</p>
            <p className="nome-escola">{escola.nome}</p>
          </div>
        </div>
      </header>
      <main id="conteudo" className="conteudo" tabIndex={-1}>{children}</main>
      <footer className="rodape">
        Pagamento via Pix direto na conta da {escola.nomeApm}.
      </footer>
    </div>
  );
}
