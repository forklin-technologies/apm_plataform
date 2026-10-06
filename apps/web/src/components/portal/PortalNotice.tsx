import Link from "next/link";
import { Wordmark } from "@/components/ui/Logo";

/**
 * Tela amigavel do portal para o que nao e um 404: muitos acessos (429) ou API indisponivel.
 * Sem texto do servidor. O `<a>` recarrega a pagina (refaz a consulta no servidor).
 */
export function PortalNotice({ title, text, reloadHref }: { title: string; text: string; reloadHref: string }) {
  return (
    <div className="paper-grid flex min-h-dvh flex-col">
      <header className="mx-auto w-full max-w-6xl px-5 pt-5 sm:px-8 sm:pt-7">
        <Link href="/" className="inline-flex min-h-11 items-center rounded-lg">
          <Wordmark />
        </Link>
      </header>
      <main id="conteudo" tabIndex={-1} className="mx-auto flex w-full max-w-xl flex-1 flex-col justify-center px-5 pb-24 pt-10 outline-none">
        <h1 className="text-title sm:text-[2.25rem]">{title}</h1>
        <p role="alert" className="mt-3 text-body text-ink-2">
          {text}
        </p>
        <a
          href={reloadHref}
          className="mt-7 inline-flex min-h-[52px] items-center justify-center self-start rounded-[14px] bg-accent px-5 text-body font-semibold text-accent-contrast"
        >
          Tentar de novo
        </a>
      </main>
    </div>
  );
}
