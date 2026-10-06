import { describe, expect, it } from "vitest";
import { areaHref, areasFor, homeArea } from "./permissions";

// Permissoes REAIS de cada papel (docs/auth.md e GET /auth/me).
const STAFF = ["expenses:read_own", "expenses:submit"];
const TREASURER = ["contributions:record_cash", "expenses:approve", "expenses:read_all", "months:close", "refunds:register", "reimbursements:register", "reports:read", "statement:read"];
const SCHOOL_ADMIN = [...TREASURER, "expenses:read_own", "expenses:submit", "invitations:create", "memberships:manage", "organization:update", "reports:read_aggregate", "school:update"];
const VIEWER = ["reports:read_aggregate"];

describe("areasFor", () => {
  it("professora (staff): so 'Minhas despesas'", () => {
    expect(areasFor(STAFF)).toEqual({ resumo: false, fechamento: false, manageExpenses: false, myExpenses: true, closeMonths: false, reopenMonths: false });
    expect(homeArea(areasFor(STAFF))).toBe("despesas");
  });

  it("tesoureira: resumo, fila de analise e fechamento; fecha mes mas nao reabre; nao envia despesa propria", () => {
    expect(areasFor(TREASURER)).toEqual({ resumo: true, fechamento: true, manageExpenses: true, myExpenses: false, closeMonths: true, reopenMonths: false });
  });

  it("diretor (school_admin): tudo, menos reabrir mes; envia e analisa", () => {
    expect(areasFor(SCHOOL_ADMIN)).toMatchObject({ resumo: true, fechamento: true, manageExpenses: true, myExpenses: true, closeMonths: true, reopenMonths: false });
  });

  it("administrador da organizacao: reabrir mes so com months:reopen", () => {
    expect(areasFor([...SCHOOL_ADMIN, "months:reopen"]).reopenMonths).toBe(true);
  });

  it("viewer: resumo e fechamento so de leitura, sem despesas e sem fechar", () => {
    expect(areasFor(VIEWER)).toEqual({ resumo: true, fechamento: true, manageExpenses: false, myExpenses: false, closeMonths: false, reopenMonths: false });
    expect(homeArea(areasFor(VIEWER))).toBe("resumo");
  });

  it("sem permissao nenhuma nao ve nada e nao inventa area", () => {
    expect(Object.values(areasFor([])).every((v) => v === false)).toBe(true);
  });
});

describe("areaHref", () => {
  it("monta os caminhos com mes e aba validos", () => {
    expect(areaHref("resumo")).toBe("/painel");
    expect(areaHref("despesas")).toBe("/painel/despesas");
    expect(areaHref("despesas", { tab: "minhas" })).toBe("/painel/despesas?aba=minhas");
    expect(areaHref("fechamento", { period: "2026-09" })).toBe("/painel/fechamento?mes=2026-09");
    expect(areaHref("fechamento", { period: "2026-13" })).toBe("/painel/fechamento");
  });
});
