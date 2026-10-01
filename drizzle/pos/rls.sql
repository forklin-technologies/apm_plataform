-- Row Level Security: segunda camada de isolamento entre escolas.
-- A aplicação conecta com o papel "apm_app" (sem superusuário) e, em cada
-- transação, define app.escola_id (contexto da escola) ou app.sistema = 'on'
-- (jobs e super admin, sempre registrados em auditoria).
-- Este arquivo é idempotente: pode ser executado a cada migração.

CREATE OR REPLACE FUNCTION app_escola() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT nullif(current_setting('app.escola_id', true), '')::uuid $$;

CREATE OR REPLACE FUNCTION app_sistema() RETURNS boolean LANGUAGE sql STABLE AS
$$ SELECT coalesce(current_setting('app.sistema', true), '') = 'on' $$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['credenciais_pix','turmas','campanhas','itens','pedidos','itens_pedido',
                           'pagamentos','pix_avulsos','logs_auditoria','notificacoes','eventos_webhook']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS isolamento_escola ON %I', t);
    EXECUTE format($p$CREATE POLICY isolamento_escola ON %I
                     USING (app_sistema() OR escola_id = app_escola())
                     WITH CHECK (app_sistema() OR escola_id = app_escola())$p$, t);
  END LOOP;
END $$;

-- Escolas: leitura pública (página da escola), alteração só no próprio contexto.
ALTER TABLE escolas ENABLE ROW LEVEL SECURITY;
ALTER TABLE escolas FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS escolas_leitura ON escolas;
DROP POLICY IF EXISTS escolas_escrita ON escolas;
DROP POLICY IF EXISTS escolas_insercao ON escolas;
CREATE POLICY escolas_leitura ON escolas FOR SELECT USING (true);
CREATE POLICY escolas_escrita ON escolas FOR UPDATE
  USING (app_sistema() OR id = app_escola()) WITH CHECK (app_sistema() OR id = app_escola());
CREATE POLICY escolas_insercao ON escolas FOR INSERT WITH CHECK (app_sistema());

-- Permissões do papel da aplicação.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'apm_app') THEN
    GRANT USAGE ON SCHEMA public TO apm_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO apm_app;
    REVOKE UPDATE, DELETE ON logs_auditoria FROM apm_app;   -- auditoria só cresce
    REVOKE DELETE ON pagamentos, pedidos, itens_pedido, eventos_webhook FROM apm_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO apm_app;
  END IF;
END $$;
