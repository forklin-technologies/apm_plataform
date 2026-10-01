"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ApiStatusChip } from "@/components/status/ApiStatusChip";
import { ButtonLink } from "@/components/ui/Button";
import { Wordmark } from "@/components/ui/Logo";
import { PrototypeBadge } from "@/components/ui/PrototypeBadge";
import { api } from "@/lib/api";
import type { DashboardData, Organization } from "@/lib/api/types";
import type { PainelSection } from "@/lib/painel-section";
import { KIND_LABELS } from "@/lib/status";
import { Indicators } from "./Indicators";
import { KIND_META } from "./kinds";
import { MovementList } from "./MovementList";
import { NAV_ITEMS } from "./nav";
import { TenantSwitcher } from "./TenantSwitcher";
import { WeeklyChart } from "./WeeklyChart";

interface PainelShellProps {
  organizations: Organization[];
  initialSchoolId: string;
  initialData: DashboardData;
  section: PainelSection;
}

type Load = { state: "ready"; data: DashboardData } | { state: "loading" } | { state: "error" };

function Skeleton() {
  return (
    <div role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">Carregando os dados da escola…</span>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" aria-hidden="true">
        <div className="rounded-[var(--r-lg)] bg-surface p-6 shadow-[0_0_0_1px_var(--line)]">
          <div className="skeleton h-4 w-32" />
          <div className="skeleton mt-4 h-12 w-64 max-w-full" />
          <div className="skeleton mt-8 h-16 w-full" />
        </div>
        <div className="rounded-[var(--r-lg)] bg-surface p-6 shadow-[0_0_0_1px_var(--line)]">
          <div className="skeleton h-4 w-48" />
          <div className="skeleton mt-6 h-40 w-full" />
        </div>
      </div>
      <div className="mt-8 space-y-px overflow-hidden rounded-[var(--r-lg)] bg-surface shadow-[0_0_0_1px_var(--line)]" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="flex items-center gap-4 border-b border-line px-5 py-4 last:border-0">
            <div className="skeleton size-10 shrink-0 !rounded-[12px]" />
            <div className="flex-1 space-y-2">
              <div className="skeleton h-4 w-3/5" />
              <div className="skeleton h-3 w-2/5" />
            </div>
            <div className="skeleton h-5 w-20" />
          </div>
        ))}
      </div>
    </div>
  );
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

export function PainelShell({ organizations, initialSchoolId, initialData, section }: PainelShellProps) {
  const [schoolId, setSchoolId] = useState(initialSchoolId);
  const [load, setLoad] = useState<Load>({ state: "ready", data: initialData });
  const requestRef = useRef(0);
  const firstRender = useRef(true);

  const organization = organizations.find((org) => org.schools.some((s) => s.id === schoolId)) ?? organizations[0]!;
  const school = organization.schools.find((s) => s.id === schoolId) ?? organization.schools[0]!;

  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    const request = ++requestRef.current;
    let cancelled = false;
    setLoad({ state: "loading" });
    void api.dashboard.getDashboard(schoolId).then((result) => {
      if (cancelled || request !== requestRef.current) return;
      setLoad(result.ok ? { state: "ready", data: result.data } : { state: "error" });
    });
    return () => {
      cancelled = true;
    };
  }, [schoolId]);

  const title = section === "resumo" ? "Resumo" : KIND_META[section].plural;
  const data = load.state === "ready" ? load.data : null;
  const movements =
    data === null
      ? []
      : section === "resumo"
        ? data.movements.slice(0, 8)
        : data.movements.filter((m) => m.kind === section);

  const switcher = (compact: boolean) => (
    <TenantSwitcher
      organizations={organizations}
      organization={organization}
      school={school}
      onSelect={setSchoolId}
      compact={compact}
    />
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
          <PrototypeBadge />
          <ApiStatusChip />
        </div>
      </aside>

      <div className="min-w-0">
        {/* barra superior (celular) */}
        <header className="bar-material sticky top-0 z-30 border-b border-line px-4 py-2 lg:hidden">
          <div className="mx-auto flex max-w-3xl items-center gap-2">
            <div className="min-w-0 flex-1">{switcher(true)}</div>
          </div>
        </header>

        <main id="conteudo" tabIndex={-1} className="outline-none mx-auto w-full max-w-[68rem] px-5 pb-32 pt-6 lg:px-10 lg:pb-16 lg:pt-10">
          <div className="mb-6 flex flex-wrap items-start justify-between gap-x-6 gap-y-3 lg:mb-8">
            <div>
              <h1 className="text-title text-ink sm:text-[2.25rem]">{title}</h1>
              <p className="mt-1 text-body text-ink-2">
                {school.name}
                {data && section === "resumo" ? ` · ${data.summary.periodLabel}` : ""}
              </p>
            </div>
            <div className="flex flex-col items-start gap-2 lg:hidden">
              <PrototypeBadge />
            </div>
          </div>

          {load.state === "loading" && <Skeleton />}

          {load.state === "error" && (
            <div role="alert" className="rounded-[var(--r-lg)] bg-bad-soft px-6 py-5 text-bad">
              <p className="text-headline">Não foi possível carregar os dados desta escola</p>
              <p className="mt-1 text-body">Tente trocar de escola de novo em instantes.</p>
            </div>
          )}

          {data && section === "resumo" && (
            <div className="space-y-8">
              {data.movements.length === 0 ? (
                <>
                  <Indicators summary={data.summary} />
                  <EmptyState
                    title={`Nenhuma movimentação em ${data.summary.periodLabel.split(" de ")[0]?.toLowerCase()}`}
                    text="Quando as famílias contribuírem e a tesouraria registrar despesas, tudo aparece aqui, com o status de cada pagamento."
                    portalSlug={school.slug}
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

          {data && section !== "resumo" && (
            <section aria-labelledby="kind-title">
              <h2 id="kind-title" className="sr-only">
                {KIND_META[section].plural} de {school.name}
              </h2>
              <p className="mb-4 max-w-[60ch] text-body text-ink-2">{KIND_META[section].definition}</p>
              {movements.length === 0 ? (
                <EmptyState
                  title={`Nenhuma ${KIND_LABELS[section].toLowerCase()} neste período`}
                  text={`Quando houver ${KIND_META[section].plural.toLowerCase()} em ${school.name}, elas aparecem aqui, uma por linha, com o status de cada uma.`}
                  portalSlug={section === "CONTRIBUTION" ? school.slug : undefined}
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
        <ul className="mx-auto grid max-w-xl grid-cols-5">
          {NAV_ITEMS.map((item) => {
            const current = item.section === section;
            return (
              <li key={item.section}>
                <Link
                  href={item.href}
                  aria-current={current ? "page" : undefined}
                  className={`flex min-h-[3.5rem] flex-col items-center justify-center gap-0.5 px-0.5 text-tab font-medium ${current ? "text-ink" : "text-ink-2"}`}
                >
                  <span className={`grid h-7 w-12 place-items-center rounded-full transition-colors ${current ? "bg-neutral-soft" : ""}`}>
                    {item.icon}
                  </span>
                  <span className="max-w-full truncate">{item.label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}
