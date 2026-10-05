"use client";

import { Button, buttonClass } from "@/components/ui/Button";
import { Wordmark } from "@/components/ui/Logo";
import { PrototypeBadge } from "@/components/ui/PrototypeBadge";

/**
 * Tela de erro inesperado (error.tsx e global-error.tsx). Texto em portugues, com a identidade do
 * produto. NUNCA mostra error.message (pode conter detalhe interno); so o codigo (digest), que e um
 * hash, para a tesouraria poder informar a quem cuida do sistema.
 */
export function ErrorView({ reset, digest }: { reset: () => void; digest?: string }) {
  return (
    <div className="paper-grid flex min-h-dvh flex-col">
      <header className="mx-auto w-full max-w-6xl px-5 pt-5 sm:px-8 sm:pt-7">
        <Wordmark />
      </header>
      <main id="conteudo" tabIndex={-1} className="mx-auto flex w-full max-w-6xl flex-1 flex-col justify-center px-5 pb-24 pt-10 outline-none sm:px-8">
        <PrototypeBadge className="self-start" />
        <h1 className="mt-8 max-w-[20ch] text-balance text-title sm:text-[2.5rem]">Algo deu errado por aqui</h1>
        <p className="mt-3 max-w-[46ch] text-body text-ink-2">
          Não conseguimos mostrar esta página. Nada foi cobrado nem enviado. Tente de novo e, se continuar, volte ao início.
        </p>
        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          <Button onClick={reset}>Tentar de novo</Button>
          {/* <a> e nao <Link>: um erro pode deixar o roteador num estado ruim; recarregar limpa tudo. */}
          {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
          <a href="/" className={buttonClass("secondary", "lg")}>
            Ir para o início
          </a>
        </div>
        {digest && <p className="mt-6 text-foot text-ink-2">Código do erro: {digest}</p>}
      </main>
    </div>
  );
}
