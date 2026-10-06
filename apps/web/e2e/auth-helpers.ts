import { readFileSync } from "node:fs";
import { expect, type Page } from "@playwright/test";

/**
 * Login REAL contra a API local (docker compose, banco com seed de demonstracao). A senha dos
 * usuarios de demonstracao NAO esta no repositorio: vem do ambiente, so em tempo de execucao.
 *   E2E_DEMO_PASSWORD_FILE=/caminho/do/arquivo   ou   E2E_DEMO_PASSWORD=...
 * A API so aceita pedidos que mudam dados vindos das origens locais :3100 e :3101, entao estes
 * testes rodam contra o `npm run dev` (3101):  E2E_BASE_URL=http://127.0.0.1:3101 npm run test:e2e
 * Sem a senha, os testes que dependem de login sao PULADOS.
 */
export function demoPassword(): string | null {
  const direct = process.env.E2E_DEMO_PASSWORD?.trim();
  if (direct) return direct;
  const file = process.env.E2E_DEMO_PASSWORD_FILE;
  if (!file) return null;
  try {
    return readFileSync(file, "utf8").trim() || null;
  } catch {
    return null;
  }
}

export const ANA = { email: "ana.admin@example.test", name: "Ana Administradora (demo)", organization: "Rede Escolar Demo" };
/** Tesoureira da Escola Aurora (demo): tem statement:read, contributions:record_cash e reports:read. */
export const CARLA = { email: "carla.tesoureira@example.test", name: "Carla Tesoureira (demo)", organization: "Rede Escolar Demo", school: "Escola Aurora (demo)" };
/** Leitora da Escola Horizonte (demo): so reports:read_aggregate (o resumo do mes). */
export const ELISA = { email: "elisa.leitora@example.test", school: "Escola Horizonte (demo)" };

export async function loginAs(page: Page, email = CARLA.email): Promise<void> {
  const password = demoPassword();
  if (!password) throw new Error("Defina E2E_DEMO_PASSWORD_FILE (ou E2E_DEMO_PASSWORD) para os testes com login.");
  await page.goto("/login");
  await page.getByLabel("E-mail").fill(email);
  await page.getByLabel("Senha").fill(password);
  await page.getByRole("button", { name: "Entrar" }).click();
  // quem nao ve o resumo (professora) cai em /painel/despesas
  await expect(page).toHaveURL(/\/painel(\/despesas)?$/);
}

/** A API so aceita pedidos que mudam dados das origens :3100 e :3101 (PUBLIC_ORIGINS de desenvolvimento). */
export function apiAcceptsThisOrigin(): boolean {
  return /^http:\/\/(127\.0\.0\.1|localhost):310[01]$/.test(process.env.E2E_BASE_URL ?? "");
}

export const PAULA = { email: "paula.professora@example.test", name: "Paula Professora (demo)" };
export const BRUNO = { email: "bruno.diretor@example.test", name: "Bruno Diretor (demo)" };

/** Identificador unico por execucao: o banco de demonstracao e compartilhado e persistente. */
export function uniqueTag(prefix = "E2E"): string {
  return `${prefix} ${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

/** Um PDF minimo e unico (a API reconhece o tipo pelos primeiros bytes; o mesmo arquivo so entra uma vez por despesa). */
export function tinyPdf(tag: string): { name: string; mimeType: string; buffer: Buffer } {
  return { name: "nota-e2e.pdf", mimeType: "application/pdf", buffer: Buffer.from(`%PDF-1.4\n% ${tag}\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n`) };
}

/** Sessao de API (sem navegador) para preparar ou desfazer dados de demonstracao. So em tempo de execucao. */
export async function apiSession(playwright: import("@playwright/test").PlaywrightWorkerArgs["playwright"], baseURL: string, email: string) {
  const context = await playwright.request.newContext({ baseURL, extraHTTPHeaders: { Origin: baseURL } });
  const login = await context.post("/api/v1/auth/login", { data: { email, password: demoPassword()! } });
  if (!login.ok()) throw new Error(`login de ${email} falhou (${login.status()})`);
  const state = await context.storageState();
  const csrf = state.cookies.find((c) => c.name === "apm_csrf")?.value ?? "";
  const body = (await login.json()) as { active_membership: { school: { id: string } | null } | null; memberships: Array<{ school: { id: string } | null }> };
  const schoolId = body.active_membership?.school?.id ?? body.memberships.find((m) => m.school)?.school?.id ?? "";
  return { context, csrf, schoolId };
}
