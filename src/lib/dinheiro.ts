/** Utilitários de dinheiro. Internamente tudo é inteiro em centavos. */

/** 2000 -> "20.00" (formato exigido pela API Pix). */
export function centavosParaDecimal(centavos: number): string {
  if (!Number.isSafeInteger(centavos) || centavos < 0) throw new Error("Valor em centavos inválido");
  const reais = Math.floor(centavos / 100);
  const resto = centavos % 100;
  return `${reais}.${resto.toString().padStart(2, "0")}`;
}

/** "20.00" | "20.5" | "20" -> 2000. Rejeita qualquer outro formato. */
export function decimalParaCentavos(valor: string): number {
  const m = /^(\d{1,10})(?:\.(\d{1,2}))?$/.exec(valor.trim());
  if (!m) throw new Error(`Valor decimal inválido: ${valor}`);
  return Number(m[1]) * 100 + Number((m[2] ?? "0").padEnd(2, "0"));
}

const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
/** 2000 -> "R$ 20,00" */
export function formatarReais(centavos: number): string {
  return brl.format(centavos / 100).replace(/\u00a0/g, " ");
}
