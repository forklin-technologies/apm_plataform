import type { MovementKind } from "./status";

/** Secoes do painel: o resumo e uma lista por tipo do dominio (4 tipos DISTINTOS). */
export type PainelSection = "resumo" | MovementKind;

const SLUG_TO_KIND: Record<string, MovementKind> = {
  contribuicoes: "CONTRIBUTION",
  despesas: "EXPENSE",
  reembolsos: "REIMBURSEMENT",
  devolucoes: "REFUND",
};

export const KIND_TO_SLUG: Record<MovementKind, string> = {
  CONTRIBUTION: "contribuicoes",
  EXPENSE: "despesas",
  REIMBURSEMENT: "reembolsos",
  REFUND: "devolucoes",
};

export function parseSection(raw: string | string[] | undefined): PainelSection {
  const value = Array.isArray(raw) ? raw[0] : raw;
  return value !== undefined && Object.hasOwn(SLUG_TO_KIND, value) ? SLUG_TO_KIND[value]! : "resumo";
}

export function sectionHref(section: PainelSection): string {
  return section === "resumo" ? "/painel" : `/painel?tipo=${KIND_TO_SLUG[section]}`;
}
