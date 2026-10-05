/**
 * Unico ponto de entrada para dados. Componentes importam `api` daqui e nunca de src/mocks.
 *
 * - `health` e `auth` sao REAIS (mesma origem, /api/*): GET /api/health/ready e a autenticacao
 *   (login, logout, me, context, password, aceite de convite; docs/auth.md).
 * - `schools`, `contributions` e `dashboard` sao SIMULADOS por src/mocks atras das interfaces
 *   de types.ts, porque a API ainda nao tem esses endpoints. Quando tiver, so este arquivo muda.
 */
import { mockDataSource } from "@/mocks";
import { acceptInvitation, changePassword, getMe, login, logout, switchContext } from "./auth";
import { getReadiness } from "./health";
import type { DataSource } from "./types";

export const IS_PROTOTYPE_DATA = true;

export const api: DataSource & {
  health: { ready: typeof getReadiness };
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
  auth: { login, logout, me: getMe, switchContext, changePassword, acceptInvitation },
  ...mockDataSource,
};

export { DEMO_RECEIPT_TOKEN } from "./token";
export type { DataSource } from "./types";
export * from "./types";
