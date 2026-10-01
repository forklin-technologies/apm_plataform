/**
 * Acesso ao banco com contexto de escola.
 *
 * Toda operação de negócio passa por `comEscola` (contexto de uma escola) ou
 * `comoSistema` (jobs/super admin). Ambos abrem uma transação e definem as
 * variáveis que o Row Level Security do PostgreSQL usa para filtrar as linhas.
 */
import { Pool } from "pg";
import { drizzle, type NodePgDatabase } from "drizzle-orm/node-postgres";
import { sql } from "drizzle-orm";
import * as schema from "./schema";

export type Db = NodePgDatabase<typeof schema>;
export type Tx = Parameters<Parameters<Db["transaction"]>[0]>[0];

const g = globalThis as unknown as { __apmPool?: Pool; __apmDb?: Db };

export function db(): Db {
  if (!g.__apmDb) {
    const url = process.env.DATABASE_URL;
    if (!url) throw new Error("DATABASE_URL não definida");
    g.__apmPool = new Pool({ connectionString: url, max: Number(process.env.DB_POOL_MAX ?? 10) });
    g.__apmDb = drizzle(g.__apmPool, { schema });
  }
  return g.__apmDb;
}

export async function encerrarDb() {
  await g.__apmPool?.end();
  g.__apmPool = undefined;
  g.__apmDb = undefined;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Executa `fn` numa transação restrita aos dados de uma escola. */
export async function comEscola<T>(escolaId: string, fn: (tx: Tx) => Promise<T>): Promise<T> {
  if (!UUID.test(escolaId)) throw new Error("escolaId inválido");
  return db().transaction(async (tx) => {
    await tx.execute(sql`select set_config('app.escola_id', ${escolaId}, true), set_config('app.sistema', '', true)`);
    return fn(tx);
  });
}

/** Executa `fn` com acesso a todas as escolas. Uso restrito a jobs e super admin. */
export async function comoSistema<T>(fn: (tx: Tx) => Promise<T>): Promise<T> {
  return db().transaction(async (tx) => {
    await tx.execute(sql`select set_config('app.sistema', 'on', true), set_config('app.escola_id', '', true)`);
    return fn(tx);
  });
}
