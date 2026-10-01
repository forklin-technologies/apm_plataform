import { isPlausibleSlug } from "@/lib/api/token";
import type { SchoolsApi } from "@/lib/api/types";
import { SCHOOLS } from "./fixtures";

/** SIMULA GET da escola publica pelo slug. Slug desconhecido: not-found (sem enumeracao). */
export const mockSchools: SchoolsApi = {
  async getPublicSchool(slug) {
    const school = isPlausibleSlug(slug) ? SCHOOLS.find((s) => s.slug === slug) : undefined;
    if (!school) return { ok: false, error: { kind: "not-found", status: 404 } };
    return { ok: true, data: school };
  },
};
