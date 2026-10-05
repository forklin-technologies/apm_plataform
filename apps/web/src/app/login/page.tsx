import type { Metadata } from "next";
import Link from "next/link";
import { LoginForm } from "@/components/login/LoginForm";
import { Wordmark } from "@/components/ui/Logo";
import { safeNext } from "@/lib/safe-next";

export const metadata: Metadata = { title: "Entrar" };

type Props = { searchParams: Promise<{ next?: string | string[] }> };

export default async function LoginPage({ searchParams }: Props) {
  const { next } = await searchParams;
  return (
    <div className="paper-grid flex min-h-dvh flex-col">
      <header className="mx-auto w-full max-w-6xl px-5 pt-5 sm:px-8 sm:pt-7">
        <Link href="/" className="inline-flex min-h-11 items-center rounded-lg">
          <Wordmark />
        </Link>
      </header>
      <main id="conteudo" tabIndex={-1} className="outline-none mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-5 pb-20 pt-8">
        <h1 className="text-title sm:text-[2.25rem]">Entrar na tesouraria</h1>
        <p className="mt-2 text-body text-ink-2">Acesso para quem cuida das contas da APM. Famílias não precisam de login para contribuir.</p>

        <LoginForm next={safeNext(next)} />

        <p className="mt-8 text-center text-sub text-ink-2">
          Recebeu um convite? Abra o link que veio no seu e-mail para criar a sua conta.
        </p>
      </main>
    </div>
  );
}
