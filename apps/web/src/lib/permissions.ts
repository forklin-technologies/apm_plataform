/**
 * O que cada area do painel pede. As permissoes vem de `active_membership.permissions` (GET /auth/me e
 * POST /auth/context): o cliente nao inventa nenhuma. Serve so para mostrar ou esconder; o servidor
 * confere de novo a cada pedido e continua sendo a autoridade (403, 404, 409 e 422 tem texto proprio).
 */
export interface Areas {
  /** Resumo, lancamentos e pendencias (statement:read) ou so os totais (reports:read_aggregate). */
  resumo: boolean;
  /** Fechamento mensal: leitura. */
  fechamento: boolean;
  /** Fila de analise da gestao: aprovar, recusar, pedir correcao, reembolsar e pagar. */
  manageExpenses: boolean;
  /** "Minhas despesas": quem pode enviar despesas. */
  myExpenses: boolean;
  closeMonths: boolean;
  reopenMonths: boolean;
}

export function areasFor(permissions: readonly string[]): Areas {
  const has = (p: string) => permissions.includes(p);
  const aggregate = has("statement:read") || has("reports:read_aggregate");
  return {
    resumo: aggregate,
    fechamento: aggregate,
    manageExpenses: has("expenses:approve") || has("reimbursements:register") || has("expenses:read_all"),
    myExpenses: has("expenses:submit"),
    closeMonths: has("months:close"),
    reopenMonths: has("months:reopen"),
  };
}

export type PainelArea = "resumo" | "despesas" | "fechamento";

export function areaHref(area: PainelArea, params: { period?: string; tab?: "minhas" } = {}): string {
  const base = area === "resumo" ? "/painel" : `/painel/${area}`;
  const query: string[] = [];
  if (params.tab) query.push(`aba=${params.tab}`);
  if (params.period && /^\d{4}-(0[1-9]|1[0-2])$/.test(params.period)) query.push(`mes=${params.period}`);
  return query.length > 0 ? `${base}?${query.join("&")}` : base;
}

/** Para onde mandar quem abre /painel sem poder ver o resumo (ex.: professora): a sua area. */
export function homeArea(areas: Areas): PainelArea {
  if (areas.resumo) return "resumo";
  if (areas.myExpenses || areas.manageExpenses) return "despesas";
  return "resumo";
}
