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
  await expect(page).toHaveURL(/\/painel$/);
}

/** A API so aceita pedidos que mudam dados das origens :3100 e :3101 (PUBLIC_ORIGINS de desenvolvimento). */
export function apiAcceptsThisOrigin(): boolean {
  return /^http:\/\/(127\.0\.0\.1|localhost):310[01]$/.test(process.env.E2E_BASE_URL ?? "");
}
