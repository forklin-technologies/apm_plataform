"use client";

import { useId, useState } from "react";
import type { WeeklyTotal } from "@/lib/api/types";
import { niceScale } from "@/lib/chart";
import { formatBRL, formatBRLWhole } from "@/lib/money";

/**
 * Entradas e saidas por semana. Colunas finas (<= 24px, topo arredondado), 2px de ar entre elas,
 * legenda sempre presente (2 series), rotulo so no maior valor, tooltip no foco e no hover,
 * e uma tabela equivalente. Os totais ja chegam prontos do backend; aqui so se desenha.
 */
export function WeeklyChart({ weekly, periodLabel }: { weekly: WeeklyTotal[]; periodLabel: string }) {
  const titleId = useId();
  const [active, setActive] = useState<number | null>(null);

  const highest = Math.max(0, ...weekly.flatMap((w) => [w.inCents, w.outCents]));
  const { max, ticks } = niceScale(highest);
  const peakIndex = weekly.findIndex((w) => w.inCents === Math.max(...weekly.map((x) => x.inCents)));
  const pct = (cents: number) => (cents <= 0 ? 0 : Math.max(1.2, (cents / max) * 100));

  return (
    <figure aria-labelledby={titleId} className="rounded-[var(--r-lg)] bg-surface p-5 shadow-[0_0_0_1px_var(--line)] sm:p-6">
      <figcaption>
        <h3 id={titleId} className="text-headline text-ink">
          Entradas e saídas por semana
        </h3>
        <p className="mt-0.5 text-foot text-ink-2">{periodLabel}, em reais</p>
        <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-sub text-ink-2">
          <li className="flex items-center gap-2">
            <span aria-hidden="true" className="size-3 rounded-[3px] bg-[var(--series-in)]" />
            Entradas
          </li>
          <li className="flex items-center gap-2">
            <span aria-hidden="true" className="size-3 rounded-[3px] bg-[var(--series-out)]" />
            Saídas
          </li>
        </ul>
      </figcaption>

      <div className="mt-9 flex gap-2">
        <div aria-hidden="true" className="relative h-44 w-[3.75rem] shrink-0 text-foot tabular-nums text-ink-3">
          {ticks.map((tick) => (
            <span
              key={tick}
              className="absolute right-0 -translate-y-1/2 whitespace-nowrap"
              style={{ bottom: `${(tick / max) * 100}%` }}
            >
              {formatBRLWhole(tick)}
            </span>
          ))}
        </div>

        <div className="min-w-0 flex-1">
          <div className="relative h-44">
            {ticks.map((tick) => (
              <span
                key={tick}
                aria-hidden="true"
                className="absolute inset-x-0 border-t border-line"
                style={{ bottom: `${(tick / max) * 100}%` }}
              />
            ))}
            <div className="relative grid h-full" style={{ gridTemplateColumns: `repeat(${weekly.length}, minmax(0, 1fr))` }}>
              {weekly.map((week, index) => {
                const label = `Semana de ${week.label}: entradas ${formatBRL(week.inCents)}, saídas ${formatBRL(week.outCents)}`;
                const edge = index === 0 ? "left-0" : index === weekly.length - 1 ? "right-0" : "left-1/2 -translate-x-1/2";
                return (
                  <div
                    key={week.label}
                    role="group"
                    tabIndex={0}
                    aria-label={label}
                    onPointerEnter={() => setActive(index)}
                    onPointerLeave={() => setActive(null)}
                    onPointerDown={() => setActive(index)}
                    onFocus={() => setActive(index)}
                    onBlur={() => setActive(null)}
                    className="relative flex h-full items-end justify-center gap-0.5 rounded-md px-1 outline-offset-[-2px]"
                  >
                    {index === peakIndex && week.inCents > 0 && (
                      <span
                        aria-hidden="true"
                        className="absolute left-1/2 -translate-x-1/2 whitespace-nowrap text-foot font-semibold text-ink"
                        style={{ bottom: `calc(${pct(week.inCents)}% + 6px)` }}
                      >
                        {formatBRLWhole(week.inCents)}
                      </span>
                    )}
                    <span
                      aria-hidden="true"
                      className="block w-full max-w-6 rounded-t-[4px] bg-[var(--series-in)] transition-opacity"
                      style={{ height: `${pct(week.inCents)}%`, opacity: active === null || active === index ? 1 : 0.55 }}
                    />
                    <span
                      aria-hidden="true"
                      className="block w-full max-w-6 rounded-t-[4px] bg-[var(--series-out)] transition-opacity"
                      style={{ height: `${pct(week.outCents)}%`, opacity: active === null || active === index ? 1 : 0.55 }}
                    />
                    {active === index && (
                      <div
                        aria-hidden="true"
                        className={`pointer-events-none absolute top-0 z-10 w-44 -translate-y-[calc(100%+6px)] rounded-[var(--r-md)] bg-raised p-3 text-left shadow-[var(--shadow-float)] ${edge}`}
                      >
                        <p className="text-foot text-ink-2">Dias {week.label}</p>
                        <p className="mt-1 flex items-center justify-between gap-3 text-sub">
                          <span className="font-semibold tabular-nums text-ink">{formatBRL(week.inCents)}</span>
                          <span className="flex items-center gap-1.5 text-ink-2">
                            <span className="h-[3px] w-3 rounded-full bg-[var(--series-in)]" />
                            Entradas
                          </span>
                        </p>
                        <p className="flex items-center justify-between gap-3 text-sub">
                          <span className="font-semibold tabular-nums text-ink">{formatBRL(week.outCents)}</span>
                          <span className="flex items-center gap-1.5 text-ink-2">
                            <span className="h-[3px] w-3 rounded-full bg-[var(--series-out)]" />
                            Saídas
                          </span>
                        </p>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
          <div
            aria-hidden="true"
            className="mt-2 grid text-center text-foot text-ink-2"
            style={{ gridTemplateColumns: `repeat(${weekly.length}, minmax(0, 1fr))` }}
          >
            {weekly.map((week) => (
              <span key={week.label}>{week.label}</span>
            ))}
          </div>
        </div>
      </div>

      <details className="mt-4 text-sub">
        <summary className="inline-flex min-h-11 items-center font-semibold text-accent-ink">Ver como tabela</summary>
        <table className="mt-2 w-full text-left text-sub">
          <caption className="sr-only">Entradas e saídas por semana em {periodLabel}</caption>
          <thead>
            <tr className="border-b border-line text-foot text-ink-2">
              <th scope="col" className="py-2 pr-3 font-medium">
                Dias
              </th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">
                Entradas
              </th>
              <th scope="col" className="py-2 text-right font-medium">
                Saídas
              </th>
            </tr>
          </thead>
          <tbody>
            {weekly.map((week) => (
              <tr key={week.label} className="border-b border-line last:border-0">
                <th scope="row" className="py-2 pr-3 font-normal text-ink">
                  {week.label}
                </th>
                <td className="py-2 pr-3 text-right tabular-nums text-ink">{formatBRL(week.inCents)}</td>
                <td className="py-2 text-right tabular-nums text-ink">{formatBRL(week.outCents)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
