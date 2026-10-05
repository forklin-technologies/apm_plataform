import { ButtonLink, buttonClass } from "@/components/ui/Button";
import { Wordmark } from "@/components/ui/Logo";

/** O servidor nao conseguiu confirmar a sessao (API fora do ar ou resposta inesperada). Nao e logout. */
export function SessionUnavailable() {
  return (
    <div className="paper-grid flex min-h-dvh flex-col">
      <header className="mx-auto w-full max-w-6xl px-5 pt-5 sm:px-8 sm:pt-7">
        <Wordmark />
      </header>
      <main id="conteudo" tabIndex={-1} className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-5 pb-20 pt-8 outline-none">
        <h1 className="text-title sm:text-[2.25rem]">Não foi possível abrir o painel</h1>
        <p role="alert" className="mt-2 text-body text-ink-2">
          Não conseguimos confirmar a sua sessão agora. Isso não encerra o seu acesso. Tente de novo em instantes.
        </p>
        {/* <a> e nao <Link>: recarregar a pagina refaz a consulta no servidor. */}
        <a href="/painel" className={buttonClass("primary", "lg", "mt-6 self-start")}>
          Tentar de novo
        </a>
        <ButtonLink href="/login" variant="plain" size="md" className="mt-2 self-start">
          Ir para a entrada
        </ButtonLink>
      </main>
    </div>
  );
}
