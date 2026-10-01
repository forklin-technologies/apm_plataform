/**
 * Dinheiro: sempre inteiro em centavos. Nunca ponto flutuante.
 * Este e o unico formatador BRL do projeto.
 */
export type Cents = number;

const NBSP = " ";

/** Maior valor aceito nos campos de entrada: R$ 9.999.999,99. */
export const MAX_INPUT_CENTS: Cents = 999_999_999;

export function isCents(value: unknown): value is Cents {
  return typeof value === "number" && Number.isSafeInteger(value);
}

function groupThousands(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}

/** "1.234,56" (sem simbolo). Aceita negativos. */
export function formatBRLNumber(cents: Cents): string {
  if (!isCents(cents)) {
    throw new TypeError("Valor monetario deve ser inteiro em centavos.");
  }
  const abs = Math.abs(cents);
  const reais = Math.trunc(abs / 100);
  const rest = abs % 100;
  const sign = cents < 0 ? "-" : "";
  return `${sign}${groupThousands(String(reais))},${String(rest).padStart(2, "0")}`;
}

/** "R$ 1.234,56" com espaco sem quebra. Negativo: "-R$ 12,50". */
export function formatBRL(cents: Cents): string {
  const text = formatBRLNumber(cents);
  return text.startsWith("-") ? `-R$${NBSP}${text.slice(1)}` : `R$${NBSP}${text}`;
}

/**
 * Converte o texto digitado em centavos, sem float.
 * Aceita "12", "12,5", "12,50", "1.234,56" e "R$ 12,00". Rejeita o resto (null).
 */
export function parseBRLToCents(input: string): Cents | null {
  const cleaned = input.replace(/R\$/gi, "").replace(/[\s ]/g, "");
  if (cleaned === "") return null;
  const match = /^(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?$/.exec(cleaned);
  if (!match) return null;
  const whole = (match[1] ?? "").replace(/\./g, "");
  const fraction = (match[2] ?? "").padEnd(2, "0");
  const cents = Number(whole) * 100 + Number(fraction);
  return isCents(cents) ? cents : null;
}

/** Campo mascarado: so os digitos importam e viram centavos (limitado a MAX_INPUT_CENTS). */
export function centsFromDigits(raw: string): Cents {
  const digits = raw.replace(/\D/g, "").replace(/^0+/, "");
  if (digits === "") return 0;
  return Math.min(Number(digits.slice(0, 12)), MAX_INPUT_CENTS);
}
