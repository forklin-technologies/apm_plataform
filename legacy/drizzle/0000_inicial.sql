CREATE TYPE "public"."forma_pagamento" AS ENUM('PIX', 'DINHEIRO');--> statement-breakpoint
CREATE TYPE "public"."modo_identificacao" AS ENUM('IDENTIFICADA', 'PARCIAL', 'ANONIMA');--> statement-breakpoint
CREATE TYPE "public"."perfil_usuario" AS ENUM('SUPER_ADMIN', 'ADMIN_ESCOLA', 'FINANCEIRO', 'OPERADOR', 'LEITURA');--> statement-breakpoint
CREATE TYPE "public"."status_campanha" AS ENUM('RASCUNHO', 'ATIVA', 'PAUSADA', 'ENCERRADA');--> statement-breakpoint
CREATE TYPE "public"."status_entrega" AS ENUM('NAO_SE_APLICA', 'PENDENTE', 'ENTREGUE');--> statement-breakpoint
CREATE TYPE "public"."status_pagamento" AS ENUM('CRIADO', 'ATIVO', 'CONCLUIDO', 'EXPIRADO', 'REMOVIDO', 'DEVOLVIDO', 'FALHOU');--> statement-breakpoint
CREATE TYPE "public"."status_pedido" AS ENUM('AGUARDANDO_PAGAMENTO', 'PIX_GERADO', 'PAGO', 'CANCELADO', 'EXPIRADO', 'ESTORNADO');--> statement-breakpoint
CREATE TYPE "public"."tipo_item" AS ENUM('COTA', 'VALOR_LIVRE', 'CONTRIBUICAO_MENSAL', 'PRODUTO');--> statement-breakpoint
CREATE TABLE "campanhas" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"escola_id" uuid NOT NULL,
	"slug" text NOT NULL,
	"nome" text NOT NULL,
	"descricao" text,
	"texto_apresentacao" text,
	"imagem_url" text,
	"data_inicio" timestamp with time zone NOT NULL,
	"data_fim" timestamp with time zone,
	"status" "status_campanha" DEFAULT 'RASCUNHO' NOT NULL,
	"meta_centavos" integer,
	"valor_minimo_centavos" integer,
	"ordem" integer DEFAULT 0 NOT NULL,
	"criado_em" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "credenciais_pix" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"escola_id" uuid NOT NULL,
	"provedor" text NOT NULL,
	"ambiente" text NOT NULL,
	"chave_pix" text NOT NULL,
	"client_id_cifrado" text NOT NULL,
	"client_secret_cifrado" text NOT NULL,
	"app_key_cifrada" text,
	"certificado_cifrado" "bytea",
	"senha_certificado_cifrada" text,
	"segredo_webhook" text NOT NULL,
	"webhook_registrado_em" timestamp with time zone,
	"atualizado_em" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "credenciais_pix_escola_id_unique" UNIQUE("escola_id")
);
--> statement-breakpoint
CREATE TABLE "escolas" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"slug" text NOT NULL,
	"nome" text NOT NULL,
	"nome_apm" text NOT NULL,
	"cnpj_apm" text NOT NULL,
	"logo_url" text,
	"endereco" text,
	"telefone" text,
	"email" text,
	"cor_primaria" text DEFAULT '#E6457A' NOT NULL,
	"cor_secundaria" text DEFAULT '#3BA7E0' NOT NULL,
	"mensagem_inicial" text,
	"mensagem_agradecimento" text,
	"modo_identificacao" "modo_identificacao" DEFAULT 'IDENTIFICADA' NOT NULL,
	"campos_formulario" jsonb NOT NULL,
	"valor_minimo_livre_centavos" integer DEFAULT 500 NOT NULL,
	"valor_maximo_livre_centavos" integer DEFAULT 100000 NOT NULL,
	"expiracao_pix_segundos" integer DEFAULT 1800 NOT NULL,
	"proximo_numero_pedido" integer DEFAULT 1 NOT NULL,
	"ativa" boolean DEFAULT true NOT NULL,
	"criado_em" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "escolas_slug_unique" UNIQUE("slug")
);
--> statement-breakpoint
CREATE TABLE "eventos_webhook" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"escola_id" uuid,
	"provedor" text NOT NULL,
	"recebido_em" timestamp with time zone DEFAULT now() NOT NULL,
	"ip" text,
	"corpo" jsonb,
	"valido" boolean NOT NULL,
	"processado_em" timestamp with time zone,
	"erro" text
);
--> statement-breakpoint
CREATE TABLE "itens" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"escola_id" uuid NOT NULL,
	"campanha_id" uuid NOT NULL,
	"tipo" "tipo_item" NOT NULL,
	"nome" text NOT NULL,
	"descricao" text,
	"imagem_url" text,
	"preco_centavos" integer,
	"controla_estoque" boolean DEFAULT false NOT NULL,
	"estoque_total" integer,
	"estoque_reservado" integer DEFAULT 0 NOT NULL,
	"estoque_vendido" integer DEFAULT 0 NOT NULL,
	"limite_por_pedido" integer,
	"inicio_venda" timestamp with time zone,
	"fim_venda" timestamp with time zone,
	"ativo" boolean DEFAULT true NOT NULL,
	"ordem" integer DEFAULT 0 NOT NULL,
	"criado_em" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "preco_coerente" CHECK (("itens"."tipo" = 'VALOR_LIVRE') = ("itens"."preco_centavos" IS NULL)),
	CONSTRAINT "preco_positivo" CHECK ("itens"."preco_centavos" IS NULL OR "itens"."preco_centavos" > 0),
	CONSTRAINT "estoque_nao_negativo" CHECK ("itens"."estoque_reservado" >= 0 AND "itens"."estoque_vendido" >= 0),
	CONSTRAINT "estoque_coerente" CHECK (NOT "itens"."controla_estoque" OR "itens"."estoque_total" IS NOT NULL)
);
--> statement-breakpoint
CREATE TABLE "itens_pedido" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"escola_id" uuid NOT NULL,
	"pedido_id" uuid NOT NULL,
	"item_id" uuid NOT NULL,
	"campanha_id" uuid NOT NULL,
	"tipo" "tipo_item" NOT NULL,
	"descricao" text NOT NULL,
	"mes_referencia" text,
	"quantidade" integer NOT NULL,
	"valor_unitario_centavos" integer NOT NULL,
	"valor_total_centavos" integer NOT NULL,
	CONSTRAINT "quantidade_positiva" CHECK ("itens_pedido"."quantidade" > 0)
);
--> statement-breakpoint
CREATE TABLE "logs_auditoria" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"escola_id" uuid,
	"usuario_id" uuid,
	"acao" text NOT NULL,
	"entidade" text NOT NULL,
	"entidade_id" text NOT NULL,
	"antes" jsonb,
	"depois" jsonb,
	"ip" text,
	"criado_em" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "notificacoes" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"escola_id" uuid NOT NULL,
	"evento" text NOT NULL,
	"canal" text NOT NULL,
	"destino" text NOT NULL,
	"payload" jsonb NOT NULL,
	"status" text DEFAULT 'PENDENTE' NOT NULL,
	"tentativas" integer DEFAULT 0 NOT NULL,
	"criado_em" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "pagamentos" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"escola_id" uuid NOT NULL,
	"pedido_id" uuid NOT NULL,
	"provedor" text NOT NULL,
	"txid" text NOT NULL,
	"end_to_end_id" text,
	"valor_centavos" integer NOT NULL,
	"valor_pago_centavos" integer,
	"pix_copia_e_cola" text,
	"status" "status_pagamento" DEFAULT 'CRIADO' NOT NULL,
	"pagador_nome_cifrado" text,
	"erro" text,
	"expira_em" timestamp with time zone NOT NULL,
	"pago_em" timestamp with time zone,
	"criado_em" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "pagamentos_txid_unique" UNIQUE("txid"),
	CONSTRAINT "pagamentos_end_to_end_id_unique" UNIQUE("end_to_end_id")
);
--> statement-breakpoint
CREATE TABLE "pedidos" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"escola_id" uuid NOT NULL,
	"codigo" text NOT NULL,
	"token_acesso" text NOT NULL,
	"responsavel_nome" text,
	"responsavel_email" text,
	"responsavel_telefone" text,
	"aluno_nome" text,
	"turma_id" uuid,
	"observacao" text,
	"anonimo" boolean DEFAULT false NOT NULL,
	"total_centavos" integer NOT NULL,
	"status" "status_pedido" DEFAULT 'AGUARDANDO_PAGAMENTO' NOT NULL,
	"status_entrega" "status_entrega" DEFAULT 'NAO_SE_APLICA' NOT NULL,
	"forma_pagamento" "forma_pagamento" DEFAULT 'PIX' NOT NULL,
	"estoque_liberado" boolean DEFAULT false NOT NULL,
	"requer_revisao" boolean DEFAULT false NOT NULL,
	"motivo_revisao" text,
	"expira_em" timestamp with time zone NOT NULL,
	"pago_em" timestamp with time zone,
	"lancado_por_id" uuid,
	"criado_em" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "pedidos_token_acesso_unique" UNIQUE("token_acesso"),
	CONSTRAINT "total_positivo" CHECK ("pedidos"."total_centavos" > 0)
);
--> statement-breakpoint
CREATE TABLE "pix_avulsos" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"escola_id" uuid NOT NULL,
	"end_to_end_id" text NOT NULL,
	"valor_centavos" integer NOT NULL,
	"horario" timestamp with time zone NOT NULL,
	"info_pagador" text,
	"pedido_id" uuid,
	"criado_em" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "pix_avulsos_end_to_end_id_unique" UNIQUE("end_to_end_id")
);
--> statement-breakpoint
CREATE TABLE "sessoes" (
	"id" text PRIMARY KEY NOT NULL,
	"usuario_id" uuid NOT NULL,
	"expira_em" timestamp with time zone NOT NULL,
	"ultimo_uso" timestamp with time zone DEFAULT now() NOT NULL,
	"ip" text,
	"user_agent" text
);
--> statement-breakpoint
CREATE TABLE "turmas" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"escola_id" uuid NOT NULL,
	"nome" text NOT NULL,
	"ordem" integer DEFAULT 0 NOT NULL,
	"ativa" boolean DEFAULT true NOT NULL
);
--> statement-breakpoint
CREATE TABLE "usuarios" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"escola_id" uuid,
	"nome" text NOT NULL,
	"email" text NOT NULL,
	"senha_hash" text NOT NULL,
	"perfil" "perfil_usuario" NOT NULL,
	"ativo" boolean DEFAULT true NOT NULL,
	"totp_segredo_cifrado" text,
	"tentativas_falhas" integer DEFAULT 0 NOT NULL,
	"bloqueado_ate" timestamp with time zone,
	"ultimo_login" timestamp with time zone,
	"criado_em" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "usuarios_email_unique" UNIQUE("email"),
	CONSTRAINT "super_admin_sem_escola" CHECK (("usuarios"."perfil" = 'SUPER_ADMIN') = ("usuarios"."escola_id" IS NULL))
);
--> statement-breakpoint
ALTER TABLE "campanhas" ADD CONSTRAINT "campanhas_escola_id_escolas_id_fk" FOREIGN KEY ("escola_id") REFERENCES "public"."escolas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "credenciais_pix" ADD CONSTRAINT "credenciais_pix_escola_id_escolas_id_fk" FOREIGN KEY ("escola_id") REFERENCES "public"."escolas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "eventos_webhook" ADD CONSTRAINT "eventos_webhook_escola_id_escolas_id_fk" FOREIGN KEY ("escola_id") REFERENCES "public"."escolas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "itens" ADD CONSTRAINT "itens_escola_id_escolas_id_fk" FOREIGN KEY ("escola_id") REFERENCES "public"."escolas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "itens" ADD CONSTRAINT "itens_campanha_id_campanhas_id_fk" FOREIGN KEY ("campanha_id") REFERENCES "public"."campanhas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "itens_pedido" ADD CONSTRAINT "itens_pedido_escola_id_escolas_id_fk" FOREIGN KEY ("escola_id") REFERENCES "public"."escolas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "itens_pedido" ADD CONSTRAINT "itens_pedido_pedido_id_pedidos_id_fk" FOREIGN KEY ("pedido_id") REFERENCES "public"."pedidos"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "itens_pedido" ADD CONSTRAINT "itens_pedido_item_id_itens_id_fk" FOREIGN KEY ("item_id") REFERENCES "public"."itens"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "itens_pedido" ADD CONSTRAINT "itens_pedido_campanha_id_campanhas_id_fk" FOREIGN KEY ("campanha_id") REFERENCES "public"."campanhas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "logs_auditoria" ADD CONSTRAINT "logs_auditoria_escola_id_escolas_id_fk" FOREIGN KEY ("escola_id") REFERENCES "public"."escolas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "logs_auditoria" ADD CONSTRAINT "logs_auditoria_usuario_id_usuarios_id_fk" FOREIGN KEY ("usuario_id") REFERENCES "public"."usuarios"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "notificacoes" ADD CONSTRAINT "notificacoes_escola_id_escolas_id_fk" FOREIGN KEY ("escola_id") REFERENCES "public"."escolas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "pagamentos" ADD CONSTRAINT "pagamentos_escola_id_escolas_id_fk" FOREIGN KEY ("escola_id") REFERENCES "public"."escolas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "pagamentos" ADD CONSTRAINT "pagamentos_pedido_id_pedidos_id_fk" FOREIGN KEY ("pedido_id") REFERENCES "public"."pedidos"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "pedidos" ADD CONSTRAINT "pedidos_escola_id_escolas_id_fk" FOREIGN KEY ("escola_id") REFERENCES "public"."escolas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "pedidos" ADD CONSTRAINT "pedidos_turma_id_turmas_id_fk" FOREIGN KEY ("turma_id") REFERENCES "public"."turmas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "pedidos" ADD CONSTRAINT "pedidos_lancado_por_id_usuarios_id_fk" FOREIGN KEY ("lancado_por_id") REFERENCES "public"."usuarios"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "pix_avulsos" ADD CONSTRAINT "pix_avulsos_escola_id_escolas_id_fk" FOREIGN KEY ("escola_id") REFERENCES "public"."escolas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "pix_avulsos" ADD CONSTRAINT "pix_avulsos_pedido_id_pedidos_id_fk" FOREIGN KEY ("pedido_id") REFERENCES "public"."pedidos"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "sessoes" ADD CONSTRAINT "sessoes_usuario_id_usuarios_id_fk" FOREIGN KEY ("usuario_id") REFERENCES "public"."usuarios"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "turmas" ADD CONSTRAINT "turmas_escola_id_escolas_id_fk" FOREIGN KEY ("escola_id") REFERENCES "public"."escolas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "usuarios" ADD CONSTRAINT "usuarios_escola_id_escolas_id_fk" FOREIGN KEY ("escola_id") REFERENCES "public"."escolas"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "campanhas_escola_slug" ON "campanhas" USING btree ("escola_id","slug");--> statement-breakpoint
CREATE INDEX "itens_escola_campanha" ON "itens" USING btree ("escola_id","campanha_id");--> statement-breakpoint
CREATE INDEX "itens_pedido_pedido" ON "itens_pedido" USING btree ("pedido_id");--> statement-breakpoint
CREATE INDEX "logs_escola_data" ON "logs_auditoria" USING btree ("escola_id","criado_em");--> statement-breakpoint
CREATE INDEX "pagamentos_pedido" ON "pagamentos" USING btree ("pedido_id");--> statement-breakpoint
CREATE UNIQUE INDEX "pedidos_escola_codigo" ON "pedidos" USING btree ("escola_id","codigo");--> statement-breakpoint
CREATE INDEX "pedidos_escola_status_data" ON "pedidos" USING btree ("escola_id","status","criado_em");--> statement-breakpoint
CREATE UNIQUE INDEX "turmas_escola_nome" ON "turmas" USING btree ("escola_id","nome");