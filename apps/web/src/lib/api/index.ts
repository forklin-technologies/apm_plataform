/**
 * Unico ponto de entrada para dados. Componentes importam `api` daqui e nunca de src/mocks.
 *
 * - `health` e REAL (GET /api/health/ready, mesma origem).
 * - `schools`, `contributions` e `dashboard` sao SIMULADOS por src/mocks atras das interfaces
 *   de types.ts. Quando o backend publicar os endpoints (docs/web-contract-proposals.md),
 *   so este arquivo muda.
 */
import { mockDataSource } from "@/mocks";
import { getReadiness } from "./health";
import type { DataSource } from "./types";

export const IS_PROTOTYPE_DATA = true;

export const api: DataSource & { health: { ready: typeof getReadiness } } = {
  health: { ready: getReadiness },
  ...mockDataSource,
};

export type { DataSource } from "./types";
export * from "./types";
