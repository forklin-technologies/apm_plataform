import type { DashboardApi } from "@/lib/api/types";
import { DASHBOARDS, ORGANIZATIONS } from "./fixtures";
import { delay } from "./util";

/** SIMULA os totais e a lista do painel. Os numeros ja chegam prontos, como viriam do backend. */
export const mockDashboard: DashboardApi = {
  async listOrganizations() {
    await delay(150);
    return { ok: true, data: ORGANIZATIONS };
  },

  async getDashboard(schoolId) {
    await delay(650);
    const data = DASHBOARDS[schoolId];
    if (!data) return { ok: false, error: { kind: "not-found", status: 404 } };
    return { ok: true, data };
  },
};
