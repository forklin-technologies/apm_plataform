import type { Metadata } from "next";
import Link from "next/link";
import { LoginForm } from "@/components/login/LoginForm";
import { Wordmark } from "@/components/ui/Logo";
import { PrototypeBadge } from "@/components/ui/PrototypeBadge";

export const metadata: Metadata = { title: "Entrar" };

export default function LoginPage() {
  return (
    <div className="paper-grid flex min-h-dvh flex-col">
      <header className="mx-auto w-full max-w-6xl px-5 pt-5 sm:px-8 sm:pt-7">
        <Link href="/" className="inline-flex min-h-11 items-center rounded-lg">
          <Wordmark />
        </Link>
      </header>
      <main id="conteudo" tabIndex={-1} className="outline-none mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-5 pb-20 pt-8">
        <PrototypeBadge className="self-start" />
        <h1 className="mt-5 text-title sm:text-[2.25rem]">Entrar na tesouraria</h1>
        <p className="mt-2 text-body text-ink-2">Acesso para quem cuida das contas da APM. Famílias não precisam de login para contribuir.</p>

        <div className="mt-6 rounded-[var(--r-md)] bg-info-soft px-4 py-3.5 text-sub text-info" role="note">
          <p className="font-semibold">O login ainda não está disponível.</p>
          <p className="mt-0.5">A autenticação chega na Fase 1. Por enquanto, esta tela é só visual: nada é enviado.</p>
        </div>

        <LoginForm />

        <p className="mt-8 text-center text-sub text-ink-2">
          Quer ver como fica por dentro?{" "}
          <Link href="/painel" className="inline-flex min-h-11 items-center font-semibold text-ink underline underline-offset-2">
            Abrir o painel de exemplo
          </Link>
        </p>
      </main>
    </div>
  );
}
