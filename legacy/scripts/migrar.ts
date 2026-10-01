/**
 * Aplica as migrações do Drizzle e, em seguida, as políticas de RLS e permissões.
 * Usa DATABASE_ADMIN_URL (dono das tabelas).
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { Pool } from "pg";
import { drizzle } from "drizzle-orm/node-postgres";
import { migrate } from "drizzle-orm/node-postgres/migrator";

export async function migrar(url = process.env.DATABASE_ADMIN_URL) {
  if (!url) throw new Error("DATABASE_ADMIN_URL não definida");
  const pool = new Pool({ connectionString: url });
  try {
    await migrate(drizzle(pool), { migrationsFolder: path.resolve(__dirname, "../drizzle") });
    await pool.query(readFileSync(path.resolve(__dirname, "../drizzle/pos/rls.sql"), "utf8"));
  } finally {
    await pool.end();
  }
}

if (require.main === module) {
  migrar().then(() => console.log("Migrações aplicadas.")).catch((e) => { console.error(e); process.exit(1); });
}
