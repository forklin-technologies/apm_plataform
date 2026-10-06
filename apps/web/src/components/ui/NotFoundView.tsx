import { ButtonLink } from "@/components/ui/Button";
import { Wordmark } from "@/components/ui/Logo";

/** 404 estilizada. O texto nunca confirma nem nega a existencia de escolas ou pedidos (ADR-010). */
export function NotFoundView({ title, text }: { title: string; text: string }) {
  return (
    <div className="paper-grid flex min-h-dvh flex-col">
      <header className="mx-auto w-full max-w-6xl px-5 pt-5 sm:px-8 sm:pt-7">
        <Wordmark />
      </header>
      <main id="conteudo" tabIndex={-1} className="outline-none mx-auto flex w-full max-w-6xl flex-1 flex-col justify-center px-5 pb-24 pt-10 sm:px-8">
        <p aria-hidden="true" className="mt-8 text-[6.5rem] font-bold leading-none tracking-[-0.06em] text-ink-3/40 sm:text-[9rem]">
          404
        </p>
        <h1 className="mt-2 max-w-[20ch] text-balance text-title sm:text-[2.5rem]">{title}</h1>
        <p className="mt-3 max-w-[46ch] text-body text-ink-2">{text}</p>
        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          <ButtonLink href="/">Ir para o início</ButtonLink>
          <ButtonLink href="/apm/escola-exemplo" variant="secondary">
            Ver a escola de exemplo
          </ButtonLink>
        </div>
      </main>
    </div>
  );
}
