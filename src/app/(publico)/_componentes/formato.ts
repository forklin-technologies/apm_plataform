/** Formatação usada nas telas (sem dependências de servidor: pode ir para o navegador). */

export { formatarReais } from "@/lib/dinheiro";

const NOMES_MES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];

/** "2026-09" -> "setembro/2026" */
export const descreverMes = (m: string) => `${NOMES_MES[Number(m.slice(5, 7)) - 1]}/${m.slice(0, 4)}`;
export const nomeDoMes = (indice: number) => NOMES_MES[indice]!;

const dataHora = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone: "America/Sao_Paulo" });
export const formatarDataHora = (iso: string | Date) => dataHora.format(new Date(iso));

/** "20,50" | "20.5" | "20" -> 2050; null se inválido. */
export function lerReais(texto: string): number | null {
  const limpo = texto.replace(/\s|R\$/g, "").replace(/\.(?=\d{3}(\D|$))/g, "").replace(",", ".");
  const m = /^(\d{1,7})(?:\.(\d{1,2}))?$/.exec(limpo);
  if (!m) return null;
  return Number(m[1]) * 100 + Number((m[2] ?? "0").padEnd(2, "0"));
}
