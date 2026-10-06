/**
 * Camada de dados SIMULADA (so o painel, ate o extrato real chegar). So `src/lib/api` importa
 * este diretorio; componentes nao.
 */
import type { DataSource } from "@/lib/api/types";
import { mockDashboard } from "./dashboard";

export const mockDataSource: DataSource = {
  dashboard: mockDashboard,
};
