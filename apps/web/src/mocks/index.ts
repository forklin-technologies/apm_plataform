/**
 * Camada de dados SIMULADA. So `src/lib/api` importa este diretorio; componentes nao.
 */
import type { DataSource } from "@/lib/api/types";
import { mockContributions } from "./contributions";
import { mockDashboard } from "./dashboard";
import { mockSchools } from "./schools";

export const mockDataSource: DataSource = {
  schools: mockSchools,
  contributions: mockContributions,
  dashboard: mockDashboard,
};

export { DEMO_RECEIPT_TOKEN } from "@/lib/api/token";
