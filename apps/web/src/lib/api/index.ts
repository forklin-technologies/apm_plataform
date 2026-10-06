/**
 * Unico ponto de entrada para dados. Componentes importam `api` daqui.
 *
 * - `health`, `auth` e `public` sao REAIS (mesma origem, /api/*): GET /api/health/ready, a autenticacao
 *   (docs/auth.md) e o portal publico de contribuicao (docs/public-flow.md).
 * - `statement` (resumo, extrato, pendencias e contribuicao em dinheiro do painel) e REAL (docs/statement.md).
 *   Nada do site e simulado: nao ha mais camada de mocks.
 */
import { acceptInvitation, changePassword, getMe, login, logout, switchContext } from "./auth";
import { getReadiness } from "./health";
import { getPending, getStatement, getSummary, recordCashContribution } from "./statement";
import { createContribution, getContribution, getPublicSchool, getReceipt, renewCharge, sandboxPay } from "./public";

export const api: {
  health: { ready: typeof getReadiness };
  public: {
    school: typeof getPublicSchool;
    createContribution: typeof createContribution;
    contribution: typeof getContribution;
    renewCharge: typeof renewCharge;
    receipt: typeof getReceipt;
    sandboxPay: typeof sandboxPay;
  };
  statement: {
    summary: typeof getSummary;
    entries: typeof getStatement;
    pending: typeof getPending;
    recordCash: typeof recordCashContribution;
  };
  auth: {
    login: typeof login;
    logout: typeof logout;
    me: typeof getMe;
    switchContext: typeof switchContext;
    changePassword: typeof changePassword;
    acceptInvitation: typeof acceptInvitation;
  };
} = {
  health: { ready: getReadiness },
  public: {
    school: getPublicSchool,
    createContribution,
    contribution: getContribution,
    renewCharge,
    receipt: getReceipt,
    sandboxPay,
  },
  auth: { login, logout, me: getMe, switchContext, changePassword, acceptInvitation },
  statement: {
    summary: getSummary,
    entries: getStatement,
    pending: getPending,
    recordCash: recordCashContribution,
  },
};

export * from "./types";
