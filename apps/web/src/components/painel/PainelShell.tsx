"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiStatusChip } from "@/components/status/ApiStatusChip";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Wordmark } from "@/components/ui/Logo";
import { PrototypeBadge } from "@/components/ui/PrototypeBadge";
import { api } from "@/lib/api";
import type { DashboardData, SchoolRef, Session } from "@/lib/api/types";
import { describeAuthError, isSessionLost } from "@/lib/auth-messages";
import type { PainelSection } from "@/lib/painel-section";
import { ROLE_LABELS, membershipTitle } from "@/lib/roles";
import { KIND_LABELS } from "@/lib/status";
import { ContextChooser } from "./ContextChooser";
import { Indicators } from "./Indicators";
import { KIND_META } from "./kinds";
import { MovementList } from "./MovementList";
import { NAV_ITEMS } from "./nav";
import { TenantSwitcher } from "./TenantSwitcher";
import { WeeklyChart } from "./WeeklyChart";

interface PainelShellProps {
  /** REAL: quem esta logado e onde atua (GET /auth/me, lido no servidor). */
  session: Session;
  /**
   * SIMULADO: indicadores e movimentacoes ainda nao tem endpoint. Vem de src/mocks e NAO depende
   * do vinculo real: e so um exemplo do que a tela vai mostrar.
   */
  example: { school: SchoolRef; data: DashboardData };
  section: PainelSection;
}

function EmptyState({ title, text, portalSlug }: { title: string; text: string; portalSlug?: string }) {
  return (
    <div className="rounded-[var(--r-lg)] bg-surface px-6 py-12 text-center shadow-[0_0_0_1px_var(--line)]">
      <div
        aria-hidden="true"
        className="mx-auto mb-5 grid size-16 grid-cols-3 gap-px overflow-hidden rounded-[18px] bg-line p-px"
      >
        {Array.from({ length: 9 }, (_, i) => (
          <span key={i} className={i === 4 ? "bg-ink" : "bg-surface"} />
        ))}
      </div>
      <h2 className="text-heading text-ink">{title}</h2>
      <p className="mx-auto mt-2 max-w-[44ch] text-body text-ink-2">{text}</p>
      {portalSlug && (
        <ButtonLink href={`/apm/${portalSlug}`} variant="secondary" size="md" className="mt-6">
          Ver o portal de contribuição
        </ButtonLink>
      )}
    </div>
  );
}

export function PainelShell({ session, example, section }: PainelShellProps) {
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
  const data = example.data;

  const title = section === "resumo" ? "Resumo" : KIND_META[section].plural;
  const movements =
    section === "resumo" ? data.movements.slice(0, 8) : data.movements.filter((m) => m.kind === section);

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
            {NAV_ITEMS.map((item) => {
              const current = item.section === section;
              return (
                <li key={item.section}>
                  <Link
                    href={item.href}
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
          <PrototypeBadge />
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
            <div className="flex flex-col items-start gap-2 lg:hidden">
              <PrototypeBadge />
            </div>
          </div>

          {problem && (
            <p role="alert" className="mb-5 rounded-[var(--r-md)] bg-bad-soft px-4 py-3 text-sub font-medium text-bad">
              {problem}
            </p>
          )}

          <p role="note" className="mb-6 rounded-[var(--r-md)] bg-info-soft px-4 py-3 text-sub text-info">
            Protótipo: os números e as movimentações abaixo são de exemplo ({data.summary.periodLabel}). O seu nome e o
            seu vínculo vêm da sua conta, mas indicadores, movimentações, despesas e extrato ainda não estão ligados à API.
          </p>

          {section === "resumo" && (
            <div className="space-y-8">
              {data.movements.length === 0 ? (
                <>
                  <Indicators summary={data.summary} />
                  <EmptyState
                    title={`Nenhuma movimentação em ${data.summary.periodLabel.split(" de ")[0]?.toLowerCase()}`}
                    text="Quando as famílias contribuírem e a tesouraria registrar despesas, tudo aparece aqui, com o status de cada pagamento."
                    portalSlug={example.school.slug}
                  />
                </>
              ) : (
                <>
                  <div className="grid gap-5 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] xl:items-start">
                    <Indicators summary={data.summary} />
                    <WeeklyChart weekly={data.summary.weekly} periodLabel={data.summary.periodLabel} />
                  </div>
                  <section aria-labelledby="recent-title">
                    <h2 id="recent-title" className="mb-3 text-heading text-ink">
                      Movimentações recentes
                    </h2>
                    <MovementList movements={movements} label="Movimentações recentes" />
                    <p className="mt-3 text-foot text-ink-2">
                      Contribuição, despesa, reembolso e devolução são coisas diferentes: veja cada uma na barra de
                      navegação.
                    </p>
                  </section>
                </>
              )}
            </div>
          )}

          {section !== "resumo" && (
            <section aria-labelledby="kind-title">
              <h2 id="kind-title" className="sr-only">
                {KIND_META[section].plural} de exemplo
              </h2>
              <p className="mb-4 max-w-[60ch] text-body text-ink-2">{KIND_META[section].definition}</p>
              {movements.length === 0 ? (
                <EmptyState
                  title={`Nenhuma ${KIND_LABELS[section].toLowerCase()} neste período`}
                  text={`Quando houver ${KIND_META[section].plural.toLowerCase()} no exemplo, elas aparecem aqui, uma por linha, com o status de cada uma.`}
                  portalSlug={section === "CONTRIBUTION" ? example.school.slug : undefined}
                />
              ) : (
                <MovementList movements={movements} label={KIND_META[section].plural} />
              )}
            </section>
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
          {NAV_ITEMS.map((item) => {
            const current = item.section === section;
            return (
              <li key={item.section} className="flex-auto">
                <Link
                  href={item.href}
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
