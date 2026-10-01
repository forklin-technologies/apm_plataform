import Link from "next/link";

export default function NaoEncontrado() {
  return (
    <>
      <div className="faixa" aria-hidden="true" />
      <main id="conteudo" className="conteudo" tabIndex={-1}>
        <h1>Página não encontrada</h1>
        <p>Confira o link enviado pela escola. Se o problema continuar, fale com a APM.</p>
        <Link className="botao" href="/">Ir para o início</Link>
      </main>
    </>
  );
}
