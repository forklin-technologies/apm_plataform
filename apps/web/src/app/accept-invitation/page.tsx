import type { Metadata } from "next";
import Link from "next/link";
import { AcceptInvitationForm } from "@/components/invitation/AcceptInvitationForm";
import { ButtonLink } from "@/components/ui/Button";
import { Wordmark } from "@/components/ui/Logo";
import { getServerSession } from "@/lib/api/server";
import { plausibleInviteToken } from "@/lib/safe-next";

export const metadata: Metadata = { title: "Aceitar convite" };

type Props = { searchParams: Promise<{ token?: string | string[] }> };

export default async function AcceptInvitationPage({ searchParams }: Props) {
  const token = plausibleInviteToken((await searchParams).token);
  const auth = token ? await getServerSession() : null;
  const signedInAs =
    auth?.state === "authenticated" ? { fullName: auth.session.user.fullName, email: auth.session.user.email } : null;

  return (
    <div className="paper-grid flex min-h-dvh flex-col">
      <header className="mx-auto w-full max-w-6xl px-5 pt-5 sm:px-8 sm:pt-7">
        <Link href="/" className="inline-flex min-h-11 items-center rounded-lg">
          <Wordmark />
        </Link>
      </header>
      <main id="conteudo" tabIndex={-1} className="outline-none mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-5 pb-20 pt-8">
        <h1 className="text-title sm:text-[2.25rem]">Aceitar convite</h1>
        {token ? (
          <>
            <p className="mt-2 text-body text-ink-2">
              Você foi convidado para atuar na tesouraria de uma APM. Confirme abaixo para aceitar.
            </p>
            <AcceptInvitationForm token={token} signedInAs={signedInAs} />
          </>
        ) : (
          <>
            <p role="alert" className="mt-2 text-body text-ink-2">
              Este link de convite está incompleto ou inválido. Abra de novo o link que veio no seu e-mail ou peça um
              novo convite a quem convidou você.
            </p>
            <ButtonLink href="/login" variant="secondary" className="mt-6 self-start">
              Ir para a entrada
            </ButtonLink>
          </>
        )}
      </main>
    </div>
  );
}
