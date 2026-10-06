"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiStatusChip } from "@/components/status/ApiStatusChip";
import { Button } from "@/components/ui/Button";
import { Wordmark } from "@/components/ui/Logo";
import { api } from "@/lib/api";
import type { Session } from "@/lib/api/types";
import { describeAuthError, isSessionLost } from "@/lib/auth-messages";
import { sectionHref, type PainelSection } from "@/lib/painel-section";
import { ROLE_LABELS, membershipTitle } from "@/lib/roles";
import { ContextChooser } from "./ContextChooser";
import { KIND_META } from "./kinds";
import { NAV_ITEMS } from "./nav";
import { PainelData } from "./PainelData";
import { TenantSwitcher } from "./TenantSwitcher";

interface PainelShellProps {
  /** REAL: quem esta logado e onde atua (GET /auth/me, lido no servidor). */
  session: Session;
  section: PainelSection;
  /** YYYY-MM da URL (?mes=); sem ele a API usa o mes corrente no fuso da escola. */
  period?: string;
}

export function PainelShell({ session, section, period }: PainelShellProps) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function selectMembership(membershipId: string) {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    const result = await api.auth.switchContext(membershipId);
    if (result.ok) {
      router.refresh(); // o servidor le /auth/me de novo, ja com o novo vinculo
    } else if (isSessionLost(result.error)) {
      router.replace("/login");
    } else {
      setProblem(describeAuthError(result.error, "context"));
    }
    setBusy(false);
  }

  async function signOut() {
    if (signingOut) return;
    setSigningOut(true);
    setProblem(null);
    const result = await api.auth.logout();
    if (result.ok) {
      router.replace("/login");
      router.refresh();
      return;
    }
    setProblem(describeAuthError(result.error, "logout"));
    setSigningOut(false);
  }

  const active = session.activeMembership;
  if (!active) {
    return (
      <ContextChooser
        session={session}
        busy={busy}
        signingOut={signingOut}
        problem={problem}
        onSelect={selectMembership}
        onSignOut={signOut}
      />
    );
  }

  const contextName = membershipTitle(active);
  const title = section === "resumo" ? "Resumo" : KIND_META[section].plural;
  // O painel e da ESCOLA do vinculo ativo. O id vem da sessao; a API ainda confere contra o contexto.
  const schoolId = active.school?.id ?? null;
  const canReadStatement = active.permissions.includes("statement:read");
  const navItems = NAV_ITEMS.filter((item) => item.section === "resumo" || canReadStatement);

  const switcher = (compact: boolean) => (
    <TenantSwitcher
      memberships={session.memberships}
      active={active}
      onSelect={selectMembership}
      busy={busy}
      compact={compact}
    />
  );

  const signOutButton = (
    <Button variant="secondary" size="md" onClick={signOut} disabled={signingOut}>
      {signingOut ? "Saindo…" : "Sair"}
    </Button>
  );

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[17.5rem_minmax(0,1fr)]">
      {/* barra lateral (desktop) */}
      <aside className="bar-material sticky top-0 hidden h-dvh flex-col gap-6 border-r border-line px-4 py-5 lg:flex">
        <div className="px-1.5">
          <Wordmark />
        </div>
        {switcher(false)}
        <nav aria-label="Seções do painel">
          <ul className="space-y-1">
            {navItems.map((item) => {
              const current = item.section === section;
              return (
                <li key={item.section}>
                  <Link
                    href={sectionHref(item.section, period)}
                    aria-current={current ? "page" : undefined}
                    className={`flex min-h-11 items-center gap-3 rounded-[12px] px-3 text-body font-medium transition-colors ${current ? "bg-ink text-bg" : "text-ink hover:bg-neutral-soft"}`}
                  >
                    {item.icon}
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="mt-auto space-y-3 px-1.5">
          <div className="min-w-0">
            <p className="truncate text-sub font-semibold text-ink">{session.user.fullName}</p>
            <p className="truncate text-foot text-ink-2">{ROLE_LABELS[active.role]}</p>
          </div>
          {signOutButton}
          <ApiStatusChip />
        </div>
      </aside>

      <div className="min-w-0">
        {/* barra superior (celular) */}
        <header className="bar-material sticky top-0 z-30 border-b border-line px-4 py-2 lg:hidden">
          <div className="mx-auto flex max-w-3xl items-center gap-2">
            <div className="min-w-0 flex-1">{switcher(true)}</div>
            {signOutButton}
          </div>
        </header>

        <main id="conteudo" tabIndex={-1} className="outline-none mx-auto w-full max-w-[68rem] px-5 pb-32 pt-6 lg:px-10 lg:pb-16 lg:pt-10">
          <div className="mb-6 flex flex-wrap items-start justify-between gap-x-6 gap-y-3 lg:mb-8">
            <div>
              <h1 className="text-title text-ink sm:text-[2.25rem]">{title}</h1>
              <p className="mt-1 text-body text-ink-2">
                {contextName} · {ROLE_LABELS[active.role]}
              </p>
              <p className="mt-0.5 text-foot text-ink-2 lg:hidden">Conectado como {session.user.fullName}</p>
            </div>
          </div>

          {problem && (
            <p role="alert" className="mb-5 rounded-[var(--r-md)] bg-bad-soft px-4 py-3 text-sub font-medium text-bad">
              {problem}
            </p>
          )}

          {schoolId ? (
            <PainelData
              // Trocar de vinculo troca de escola: o estado dos numeros recomeca do zero.
              key={schoolId}
              schoolId={schoolId}
              section={section}
              period={period}
              permissions={active.permissions}
              onPeriodChange={(next) => router.replace(sectionHref(section, next))}
            />
          ) : (
            <div role="note" className="rounded-[var(--r-lg)] bg-surface px-6 py-10 shadow-[0_0_0_1px_var(--line)]">
              <h2 className="text-heading text-ink">Esta conta é da organização inteira</h2>
              <p className="mt-2 max-w-[56ch] text-body text-ink-2">
                O painel mostra os números de uma escola, e este vínculo não é de uma escola específica. Use uma conta de
                escola, ou peça um convite para a escola que você quer acompanhar.
              </p>
            </div>
          )}
        </main>
      </div>

      {/* barra de abas (celular) */}
      <nav
        aria-label="Seções do painel"
        className="bar-material fixed inset-x-0 bottom-0 z-30 border-t border-line pb-[env(safe-area-inset-bottom)] lg:hidden"
      >
        {/* flex-auto: cada aba ocupa a largura do proprio rotulo mais uma fatia da sobra, entao nenhum
            rotulo (nem o mais longo) e truncado de 320px para cima. */}
        <ul className="mx-auto flex max-w-xl">
          {navItems.map((item) => {
            const current = item.section === section;
            return (
              <li key={item.section} className="flex-auto">
                <Link
                  href={sectionHref(item.section, period)}
                  aria-current={current ? "page" : undefined}
                  className={`flex min-h-[3.5rem] flex-col items-center justify-center gap-0.5 px-0.5 text-tab font-medium max-[339px]:text-[0.625rem] ${current ? "text-ink" : "text-ink-2"}`}
                >
                  <span className={`grid h-7 w-12 place-items-center rounded-full transition-colors ${current ? "bg-neutral-soft" : ""}`}>
                    {item.icon}
                  </span>
                  <span className="whitespace-nowrap">{item.label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}
