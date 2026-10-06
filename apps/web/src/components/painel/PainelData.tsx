"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { api } from "@/lib/api";
import { contributionsPdfHref } from "@/lib/api/statement";
import type { ApiError, ApiResult, Page, PendingEntry, StatementEntry, StatementSummary } from "@/lib/api/types";
import { describeAuthError } from "@/lib/auth-messages";
import type { PainelSection } from "@/lib/painel-section";
import { KIND_LABELS, type MovementKind } from "@/lib/status";
import { PENDING_SECTIONS, periodLabel, pendingSectionTitle, recentPeriods, currentLocalPeriod } from "@/lib/statement-labels";
import { CashContributionForm } from "./CashContributionForm";
import { KIND_META } from "./kinds";
import { PendingRows, StatementList } from "./StatementList";
import { StatementIndicators } from "./StatementIndicators";

type Res<T> = { state: "loading" } | { state: "ready"; data: T } | { state: "forbidden" } | { state: "error"; message: string };

function toRes<T>(result: ApiResult<T>): Res<T> {
  if (result.ok) return { state: "ready", data: result.data };
  if (result.error.status === 403) return { state: "forbidden" };
  return { state: "error", message: describeError(result.error) };
}

function describeError(error: ApiError): string {
  if (error.kind === "problem" && error.code === "validation_error") return "Esse mês não é válido.";
  return describeAuthError(error, "context");
}

const LOADING: Res<never> = { state: "loading" };

function Skeleton({ label }: { label: string }) {
  return (
    <div role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">{label}</span>
      <div aria-hidden="true" className="rounded-[var(--r-lg)] bg-surface p-6 shadow-[0_0_0_1px_var(--line)]">
        <div className="skeleton h-4 w-40" />
        <div className="skeleton mt-4 h-12 w-64 max-w-full" />
        <div className="skeleton mt-8 h-16 w-full" />
      </div>
    </div>
  );
}

function Notice({ tone, children }: { tone: "info" | "bad"; children: React.ReactNode }) {
  return (
    <p
      role={tone === "bad" ? "alert" : "note"}
      className={`rounded-[var(--r-md)] px-4 py-3 text-sub ${tone === "bad" ? "bg-bad-soft font-medium text-bad" : "bg-info-soft text-info"}`}
    >
      {children}
    </p>
  );
}

function EmptyState({ title, text }: { title: string; text: string }) {
  return (
    <div className="rounded-[var(--r-lg)] bg-surface px-6 py-12 text-center shadow-[0_0_0_1px_var(--line)]">
      <h2 className="text-heading text-ink">{title}</h2>
      <p className="mx-auto mt-2 max-w-[44ch] text-body text-ink-2">{text}</p>
    </div>
  );
}

interface PainelDataProps {
  /** `school.id` do vinculo ATIVO da sessao. Nunca vem de URL nem de campo do usuario. */
  schoolId: string;
  section: PainelSection;
  /** YYYY-MM vindo da URL; sem ele a API usa o mes corrente no fuso da escola. */
  period: string | undefined;
  permissions: string[];
  onPeriodChange: (period: string) => void;
}

/**
 * Numeros REAIS da escola do vinculo ativo (docs/statement.md): resumo do mes, lancamentos por tipo
 * (com "carregar mais"), pendencias fora do saldo e as acoes da tesouraria. Cada rota que responder 403
 * (perfil `viewer` so le o resumo) vira um aviso: a tela nao quebra.
 */
export function PainelData({ schoolId, section, period, permissions, onPeriodChange }: PainelDataProps) {
  const canReadStatement = permissions.includes("statement:read");
  const canRecordCash = permissions.includes("contributions:record_cash");
  const canReadReports = permissions.includes("reports:read") || permissions.includes("reports:read_aggregate");
  const kind: MovementKind | null = section === "resumo" ? null : section;

  const [reload, setReload] = useState(0);
  const [cashOpen, setCashOpen] = useState(false);
  const [latest] = useState(() => currentLocalPeriod());

  // Cada recurso guarda a chave do pedido que o gerou: chave diferente = ainda carregando (sem setState sincrono no efeito).
  const summaryKey = `${schoolId}|${period ?? ""}|${reload}`;
  const [summaryState, setSummaryState] = useState<{ key: string; res: Res<StatementSummary> } | null>(null);
  useEffect(() => {
    let cancelled = false;
    void api.statement.summary(schoolId, period).then((result) => {
      if (!cancelled) setSummaryState({ key: summaryKey, res: toRes(result) });
    });
    return () => {
      cancelled = true;
    };
  }, [schoolId, period, summaryKey]);
  const summary: Res<StatementSummary> = summaryState?.key === summaryKey ? summaryState.res : LOADING;

  const entriesKey = `${schoolId}|${period ?? ""}|${kind ?? ""}|${reload}`;
  const [entriesState, setEntriesState] = useState<{ key: string; res: Res<Page<StatementEntry>> } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  useEffect(() => {
    if (!kind || !canReadStatement) return;
    let cancelled = false;
    void api.statement.entries(schoolId, { period, kind, limit: 50 }).then((result) => {
      if (!cancelled) setEntriesState({ key: entriesKey, res: toRes(result) });
    });
    return () => {
      cancelled = true;
    };
  }, [schoolId, period, kind, canReadStatement, entriesKey]);
  const entries: Res<Page<StatementEntry>> = entriesState?.key === entriesKey ? entriesState.res : LOADING;

  async function loadMore() {
    if (entries.state !== "ready" || !entries.data.nextCursor || loadingMore) return;
    setLoadingMore(true);
    setMoreError(null);
    const result = await api.statement.entries(schoolId, { period, kind: kind ?? undefined, limit: 50, cursor: entries.data.nextCursor });
    setLoadingMore(false);
    if (!result.ok) {
      setMoreError(describeError(result.error));
      return;
    }
    setEntriesState({
      key: entriesKey,
      res: { state: "ready", data: { items: [...entries.data.items, ...result.data.items], nextCursor: result.data.nextCursor } },
    });
  }

  const pendingKey = `${schoolId}|${reload}`;
  const [pendingState, setPendingState] = useState<{ key: string; res: Res<Page<PendingEntry>> } | null>(null);
  useEffect(() => {
    if (section !== "resumo" || !canReadStatement) return;
    let cancelled = false;
    void api.statement.pending(schoolId, { limit: 100 }).then((result) => {
      if (!cancelled) setPendingState({ key: pendingKey, res: toRes(result) });
    });
    return () => {
      cancelled = true;
    };
  }, [schoolId, section, canReadStatement, pendingKey]);
  const pending: Res<Page<PendingEntry>> = pendingState?.key === pendingKey ? pendingState.res : LOADING;

  const effectivePeriod = summary.state === "ready" ? summary.data.period : (period ?? null);
  const periods = effectivePeriod ? Array.from(new Set([...recentPeriods(latest, 12), effectivePeriod])).sort().reverse() : [];

  const toolbar = (
    <div className="mb-6 flex flex-wrap items-end gap-x-4 gap-y-3">
      {effectivePeriod && (
        <div>
          <label htmlFor="period-select" className="mb-1 block text-foot font-semibold text-ink-2">
            Mês
          </label>
          <select
            id="period-select"
            value={effectivePeriod}
            onChange={(e) => onPeriodChange(e.target.value)}
            className="min-h-11 rounded-[12px] border border-field-border bg-field px-3 text-body text-ink"
          >
            {periods.map((p) => (
              <option key={p} value={p}>
                {periodLabel(p)}
              </option>
            ))}
          </select>
        </div>
      )}
      {canRecordCash && (
        <Button variant="secondary" size="md" aria-expanded={cashOpen} onClick={() => setCashOpen((v) => !v)}>
          Registrar contribuição em dinheiro
        </Button>
      )}
      {canReadReports && summary.state === "ready" && (
        // Navegacao comum (o navegador baixa o PDF com o cookie de sessao); sem fetch nem blob.
        <a
          href={contributionsPdfHref(schoolId, summary.data.period)}
          download
          className="inline-flex min-h-11 items-center rounded-[14px] px-3 text-sub font-semibold text-accent-ink underline underline-offset-2 hover:bg-neutral-soft"
        >
          Baixar PDF das contribuições do mês
        </a>
      )}
    </div>
  );

  return (
    <div>
      {toolbar}

      {canRecordCash && cashOpen && (
        <CashContributionForm
          schoolId={schoolId}
          onRecorded={() => setReload((n) => n + 1)}
          onClose={() => setCashOpen(false)}
        />
      )}

      {section === "resumo" && (
        <div className="space-y-8">
          {summary.state === "loading" && <Skeleton label="Carregando o resumo do mês…" />}
          {summary.state === "ready" && <StatementIndicators summary={summary.data} />}
          {summary.state === "forbidden" && <Notice tone="info">O seu perfil não tem acesso aos números desta escola.</Notice>}
          {summary.state === "error" && <Notice tone="bad">{summary.message}</Notice>}

          {!canReadStatement && summary.state === "ready" && (
            <Notice tone="info">
              O seu perfil vê só os totais do mês. Os lançamentos e as pendências são da tesouraria.
            </Notice>
          )}

          {canReadStatement && <PendingBlock pending={pending} />}
        </div>
      )}

      {kind && (
        <section aria-labelledby="kind-title">
          <h2 id="kind-title" className="sr-only">
            {KIND_META[kind].plural}
            {effectivePeriod ? ` de ${periodLabel(effectivePeriod)}` : ""}
          </h2>
          <p className="mb-4 max-w-[60ch] text-body text-ink-2">{KIND_META[kind].definition}</p>
          {!canReadStatement && <Notice tone="info">O seu perfil não vê os lançamentos desta escola.</Notice>}
          {canReadStatement && entries.state === "loading" && <Skeleton label="Carregando os lançamentos…" />}
          {entries.state === "forbidden" && <Notice tone="info">O seu perfil não vê os lançamentos desta escola.</Notice>}
          {entries.state === "error" && <Notice tone="bad">{entries.message}</Notice>}
          {entries.state === "ready" &&
            (entries.data.items.length === 0 ? (
              <EmptyState
                title={`Nenhuma ${KIND_LABELS[kind].toLowerCase()} neste mês`}
                text={`Quando houver ${KIND_META[kind].plural.toLowerCase()} em ${effectivePeriod ? periodLabel(effectivePeriod) : "este mês"}, elas aparecem aqui, uma por linha, com o status de cada uma.`}
              />
            ) : (
              <>
                <StatementList entries={entries.data.items} label={KIND_META[kind].plural} />
                {moreError && (
                  <p role="alert" className="mt-3 text-sub font-medium text-bad">
                    {moreError}
                  </p>
                )}
                {entries.data.nextCursor && (
                  <div className="mt-4 text-center">
                    <Button variant="secondary" onClick={loadMore} disabled={loadingMore} aria-busy={loadingMore}>
                      {loadingMore ? "Carregando…" : "Carregar mais"}
                    </Button>
                  </div>
                )}
              </>
            ))}
        </section>
      )}
    </div>
  );
}

function PendingBlock({ pending }: { pending: Res<Page<PendingEntry>> }) {
  if (pending.state === "loading") return <Skeleton label="Carregando as pendências…" />;
  if (pending.state === "forbidden") return <Notice tone="info">O seu perfil não vê as pendências desta escola.</Notice>;
  if (pending.state === "error") return <Notice tone="bad">{pending.message}</Notice>;

  const items = pending.data.items;
  const known = new Set(PENDING_SECTIONS.map((s) => s.section));
  const groups = [
    ...PENDING_SECTIONS.map((s) => ({ ...s, rows: items.filter((e) => e.section === s.section) })),
    { section: "OTHER", title: pendingSectionTitle("OTHER"), note: "", rows: items.filter((e) => !known.has(e.section)) },
  ].filter((g) => g.rows.length > 0);

  return (
    <section aria-labelledby="pending-title">
      <h2 id="pending-title" className="text-heading text-ink">
        Pendências (fora do saldo)
      </h2>
      <p className="mb-4 mt-1 max-w-[60ch] text-body text-ink-2">
        Nada daqui entra no saldo em caixa até ser pago ou confirmado.
      </p>
      {groups.length === 0 ? (
        <EmptyState title="Nenhuma pendência" text="Quando houver algo a pagar, a conferir ou a receber, aparece aqui." />
      ) : (
        <div className="space-y-6">
          {groups.map((group) => (
            <div key={group.section}>
              <h3 className="text-headline text-ink">{group.title}</h3>
              {group.note && <p className="mb-2 text-foot text-ink-2">{group.note}</p>}
              <PendingRows entries={group.rows} label={group.title} />
            </div>
          ))}
          {pending.data.nextCursor && (
            <p className="text-foot text-ink-2">Há mais pendências do que as mostradas aqui.</p>
          )}
        </div>
      )}
    </section>
  );
}
