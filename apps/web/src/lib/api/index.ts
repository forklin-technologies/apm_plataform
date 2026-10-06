/**
 * Unico ponto de entrada para dados. Componentes importam `api` daqui e nunca de src/mocks.
 *
 * - `health`, `auth` e `public` sao REAIS (mesma origem, /api/*): GET /api/health/ready, a autenticacao
 *   (docs/auth.md) e o portal publico de contribuicao (docs/public-flow.md).
 * - `dashboard` (indicadores e movimentacoes do painel) e SIMULADO por src/mocks atras da interface de
 *   types.ts ate o painel ser ligado ao extrato real. Quando for, so este arquivo muda.
 */
import { mockDataSource } from "@/mocks";
import { acceptInvitation, changePassword, getMe, login, logout, switchContext } from "./auth";
import { getReadiness } from "./health";
import { createContribution, getContribution, getPublicSchool, getReceipt, renewCharge, sandboxPay } from "./public";
import type { DataSource } from "./types";

export const IS_PROTOTYPE_DATA = true;

export const api: DataSource & {
  health: { ready: typeof getReadiness };
  public: {
    school: typeof getPublicSchool;
    createContribution: typeof createContribution;
    contribution: typeof getContribution;
    renewCharge: typeof renewCharge;
    receipt: typeof getReceipt;
    sandboxPay: typeof sandboxPay;
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
  ...mockDataSource,
};

export type { DataSource } from "./types";
export * from "./types";
