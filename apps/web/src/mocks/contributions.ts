/**
 * SIMULA o backend de contribuicao e Pix. Quem decide o status e esta camada (como o backend
 * decidiria a partir do webhook do banco): depois de PAY_DELAY_MS o "banco" confirma. A tela
 * so consulta (polling) e exibe.
 */
import { isPlausibleToken } from "@/lib/api/token";
import type {
  ApiResult,
  ContributionsApi,
  CreateContributionInput,
  PixCharge,
  PublicSchool,
  Receipt,
} from "@/lib/api/types";
import { formatBRL } from "@/lib/money";
import type { PixChargeStatus } from "@/lib/status";
import { validateAmount, validateIdentification, type IdentificationValues } from "@/lib/validation";
import { SCHOOLS } from "./fixtures";
import { delay, hashString, randomToken, readSession, writeSession } from "./util";

export const PAY_DELAY_MS = 8_000;
export const CHARGE_TTL_MS = 10 * 60 * 1000;
const STORE_KEY = "apm-proto:orders";

interface StoredOrder {
  token: string;
  slug: string;
  amountCents: number;
  description: string;
  identification: IdentificationValues;
  createdAtMs: number;
  expiresAtMs: number;
  /** Instante em que o "banco" confirma o Pix (so existe no mock). */
  payAtMs: number;
}

const memory = new Map<string, StoredOrder>();

function load(token: string): StoredOrder | null {
  const inMemory = memory.get(token);
  if (inMemory) return inMemory;
  const stored = readSession<Record<string, StoredOrder>>(STORE_KEY, {})[token];
  if (stored) {
    memory.set(token, stored);
    return stored;
  }
  return null;
}

function save(order: StoredOrder): void {
  memory.set(order.token, order);
  const all = readSession<Record<string, StoredOrder>>(STORE_KEY, {});
  all[order.token] = order;
  writeSession(STORE_KEY, all);
}

export function resetMockOrders(): void {
  memory.clear();
  writeSession(STORE_KEY, {});
}

function findSchool(slug: string): PublicSchool | undefined {
  return SCHOOLS.find((school) => school.slug === slug);
}

function statusAt(order: StoredOrder, now: number): PixChargeStatus {
  if (now >= order.payAtMs && order.payAtMs < order.expiresAtMs) return "PAID";
  if (now >= order.expiresAtMs) return "EXPIRED";
  return "PENDING";
}

function toCharge(order: StoredOrder, now: number): PixCharge {
  const status = statusAt(order, now);
  return {
    token: order.token,
    status,
    amountCents: order.amountCents,
    // Texto OBVIAMENTE falso: nao tem o prefixo EMV do Pix, nenhum app de banco o reconhece.
    payload: `PROTOTIPO.NAO-E-PIX.NAO-PAGUE.${order.token}.VALOR-${order.amountCents}-CENTAVOS`,
    createdAt: new Date(order.createdAtMs).toISOString(),
    expiresAt: new Date(order.expiresAtMs).toISOString(),
    paidAt: status === "PAID" ? new Date(order.payAtMs).toISOString() : null,
  };
}

const SAMPLE_GUARDIANS = ["Helena Martins", "Carlos Eduardo Pires", "Juliana Rezende", "Sérgio Amaral", "Patrícia Gomes", "Wagner Lacerda"];
const SAMPLE_STUDENTS = ["Lucas Martins", "Isabela Pires", "Davi Rezende", "Sofia Amaral", "Miguel Gomes", "Alice Lacerda"];
const SAMPLE_CLASSES = ["1º A", "2º B", "3º C", "4º A", "5º B", "6º A"];

function receiptNumber(token: string): string {
  return `2026-${String(hashString(token) % 1_000_000).padStart(6, "0")}`;
}

function deterministicReceipt(school: PublicSchool, token: string): Receipt {
  const h = hashString(token);
  const quota = school.quotas[h % school.quotas.length];
  const identification: IdentificationValues = {};
  if (school.identification.guardianName !== "HIDDEN") {
    identification.guardianName = SAMPLE_GUARDIANS[h % SAMPLE_GUARDIANS.length];
  }
  if (school.identification.studentName !== "HIDDEN") {
    identification.studentName = SAMPLE_STUDENTS[(h >>> 3) % SAMPLE_STUDENTS.length];
  }
  if (school.identification.classroom !== "HIDDEN") {
    identification.classroom = SAMPLE_CLASSES[(h >>> 6) % SAMPLE_CLASSES.length];
  }
  const day = 1 + (h % 28);
  const paidAt = new Date(Date.UTC(2026, 8, day, 14 + (h % 6), h % 60)).toISOString();
  return {
    token,
    number: receiptNumber(token),
    schoolName: school.name,
    apmName: school.apmName,
    description: quota?.name ?? "Contribuição",
    amountCents: quota?.amountCents ?? 2000,
    status: "PAID",
    paidAt,
    identification,
  };
}

function fail<T>(kind: "http" | "not-found", status: number): ApiResult<T> {
  return { ok: false, error: { kind, status } };
}

export const mockContributions: ContributionsApi = {
  async create(input: CreateContributionInput) {
    await delay(500);
    const school = findSchool(input.slug);
    if (!school) return fail("not-found", 404);

    let amountCents: number;
    let description: string;
    if (input.amount.kind === "QUOTA") {
      const quotaId = input.amount.quotaId;
      const quota = school.quotas.find((q) => q.id === quotaId);
      if (!quota) return fail("http", 422);
      amountCents = quota.amountCents;
      description = quota.name;
    } else {
      // O backend real revalida: aqui o mock aplica as mesmas regras da escola.
      const check = validateAmount(input.amount.cents, school.customAmount);
      if (!check.ok) return fail("http", 422);
      amountCents = check.cents;
      description = `Valor livre de ${formatBRL(check.cents)}`;
    }

    const identification = validateIdentification(input.identification, school.identification);
    if (!identification.ok) return fail("http", 422);

    const now = Date.now();
    const order: StoredOrder = {
      token: randomToken(),
      slug: school.slug,
      amountCents,
      description,
      identification: identification.cleaned,
      createdAtMs: now,
      expiresAtMs: now + CHARGE_TTL_MS,
      payAtMs: now + PAY_DELAY_MS,
    };
    save(order);
    return { ok: true, data: { charge: toCharge(order, now) } };
  },

  async getCharge(slug, token) {
    await delay(150);
    const order = isPlausibleToken(token) ? load(token) : null;
    if (!order || order.slug !== slug) return fail("not-found", 404);
    return { ok: true, data: toCharge(order, Date.now()) };
  },

  async getReceipt(slug, token) {
    const school = findSchool(slug);
    if (!school || !isPlausibleToken(token)) return fail("not-found", 404);

    const order = load(token);
    if (order) {
      if (order.slug !== slug) return fail("not-found", 404);
      const charge = toCharge(order, Date.now());
      if (charge.status !== "PAID") return fail("http", 409);
      return {
        ok: true,
        data: {
          token,
          number: receiptNumber(token),
          schoolName: school.name,
          apmName: school.apmName,
          description: order.description,
          amountCents: order.amountCents,
          status: charge.status,
          paidAt: charge.paidAt,
          identification: order.identification,
        },
      };
    }
    // Token que o mock nao conhece (por exemplo, link de exemplo): comprovante deterministico.
    return { ok: true, data: deterministicReceipt(school, token) };
  },
};
