import type { Cents } from "./money";

/**
 * Escala "bonita" para o eixo do grafico, em centavos inteiros.
 * Devolve um teto (1, 2, 3, 4, 5, 6 ou 8 x 10^n reais) e 3 marcas: 0, metade e teto.
 */
export function niceScale(maxCents: Cents): { max: Cents; ticks: Cents[] } {
  if (!Number.isFinite(maxCents) || maxCents <= 0) {
    return { max: 10_000, ticks: [0, 5_000, 10_000] };
  }
  const reais = maxCents / 100;
  const magnitude = 10 ** Math.floor(Math.log10(reais));
  const candidates = [1, 2, 3, 4, 5, 6, 8, 10].map((m) => m * magnitude);
  const stepMax = candidates.find((c) => c >= reais) ?? 10 * magnitude;
  const max = Math.round(stepMax * 100);
  return { max, ticks: [0, Math.round(max / 2), max] };
}
