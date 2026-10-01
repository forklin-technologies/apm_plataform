import { Pool } from "pg";
import { migrar } from "../scripts/migrar";

/** Recria o banco de teste do zero (esquema + RLS). */
export async function recriarBanco() {
  const admin = new Pool({ connectionString: process.env.DATABASE_ADMIN_URL });
  await admin.query("DROP SCHEMA IF EXISTS public CASCADE; DROP SCHEMA IF EXISTS drizzle CASCADE; CREATE SCHEMA public;");
  await admin.end();
  await migrar(process.env.DATABASE_ADMIN_URL);
}

export async function limparDados() {
  const admin = new Pool({ connectionString: process.env.DATABASE_ADMIN_URL });
  await admin.query(`TRUNCATE notificacoes, logs_auditoria, pix_avulsos, eventos_webhook, pagamentos, itens_pedido,
    pedidos, itens, campanhas, turmas, sessoes, usuarios, credenciais_pix, escolas CASCADE`);
  await admin.end();
}
