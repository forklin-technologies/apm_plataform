/**
 * Esquema do banco de dados.
 *
 * Convenções:
 * - Valores monetários SEMPRE em centavos (inteiro).
 * - Toda tabela de negócio tem `escola_id` e é protegida por Row Level Security
 *   (ver drizzle/9999_rls.sql).
 * - Itens do pedido guardam cópia do nome e do preço no momento da compra.
 */
import { sql } from "drizzle-orm";
import {
  pgTable, pgEnum, uuid, text, integer, boolean, timestamp, jsonb, customType,
  uniqueIndex, index, check,
} from "drizzle-orm/pg-core";

const bytea = customType<{ data: Buffer }>({ dataType: () => "bytea" });

export const perfilUsuario = pgEnum("perfil_usuario", ["SUPER_ADMIN", "ADMIN_ESCOLA", "FINANCEIRO", "OPERADOR", "LEITURA"]);
export const statusCampanha = pgEnum("status_campanha", ["RASCUNHO", "ATIVA", "PAUSADA", "ENCERRADA"]);
export const tipoItem = pgEnum("tipo_item", ["COTA", "VALOR_LIVRE", "CONTRIBUICAO_MENSAL", "PRODUTO"]);
export const statusPedido = pgEnum("status_pedido", ["AGUARDANDO_PAGAMENTO", "PIX_GERADO", "PAGO", "CANCELADO", "EXPIRADO", "ESTORNADO"]);
export const statusEntrega = pgEnum("status_entrega", ["NAO_SE_APLICA", "PENDENTE", "ENTREGUE"]);
export const statusPagamento = pgEnum("status_pagamento", ["CRIADO", "ATIVO", "CONCLUIDO", "EXPIRADO", "REMOVIDO", "DEVOLVIDO", "FALHOU"]);
export const formaPagamento = pgEnum("forma_pagamento", ["PIX", "DINHEIRO"]);
export const modoIdentificacao = pgEnum("modo_identificacao", ["IDENTIFICADA", "PARCIAL", "ANONIMA"]);

const criadoEm = () => timestamp("criado_em", { withTimezone: true }).notNull().defaultNow();

/** Configuração de cada campo do formulário de identificação. */
export type RegraCampo = "obrigatorio" | "opcional" | "oculto";
export type CamposFormulario = {
  responsavelNome: RegraCampo;
  responsavelEmail: RegraCampo;
  responsavelTelefone: RegraCampo;
  alunoNome: RegraCampo;
  turma: RegraCampo;
  observacao: RegraCampo;
};

export const escolas = pgTable("escolas", {
  id: uuid("id").primaryKey().defaultRandom(),
  slug: text("slug").notNull().unique(),
  nome: text("nome").notNull(),
  nomeApm: text("nome_apm").notNull(),
  cnpjApm: text("cnpj_apm").notNull(),
  logoUrl: text("logo_url"),
  endereco: text("endereco"),
  telefone: text("telefone"),
  email: text("email"),
  corPrimaria: text("cor_primaria").notNull().default("#E6457A"),
  corSecundaria: text("cor_secundaria").notNull().default("#3BA7E0"),
  mensagemInicial: text("mensagem_inicial"),
  mensagemAgradecimento: text("mensagem_agradecimento"),
  modoIdentificacao: modoIdentificacao("modo_identificacao").notNull().default("IDENTIFICADA"),
  camposFormulario: jsonb("campos_formulario").$type<CamposFormulario>().notNull(),
  valorMinimoLivreCentavos: integer("valor_minimo_livre_centavos").notNull().default(500),
  valorMaximoLivreCentavos: integer("valor_maximo_livre_centavos").notNull().default(100000),
  expiracaoPixSegundos: integer("expiracao_pix_segundos").notNull().default(1800),
  proximoNumeroPedido: integer("proximo_numero_pedido").notNull().default(1),
  ativa: boolean("ativa").notNull().default(true),
  criadoEm: criadoEm(),
});

export const credenciaisPix = pgTable("credenciais_pix", {
  id: uuid("id").primaryKey().defaultRandom(),
  escolaId: uuid("escola_id").notNull().unique().references(() => escolas.id),
  provedor: text("provedor").notNull(), // "bb" | "sandbox" | ...
  ambiente: text("ambiente").notNull(), // "homologacao" | "producao"
  chavePix: text("chave_pix").notNull(),
  clientIdCifrado: text("client_id_cifrado").notNull(),
  clientSecretCifrado: text("client_secret_cifrado").notNull(),
  appKeyCifrada: text("app_key_cifrada"), // "developer application key" do BB
  certificadoCifrado: bytea("certificado_cifrado"), // .p12/.pfx para mTLS
  senhaCertificadoCifrada: text("senha_certificado_cifrada"),
  segredoWebhook: text("segredo_webhook").notNull(),
  webhookRegistradoEm: timestamp("webhook_registrado_em", { withTimezone: true }),
  atualizadoEm: timestamp("atualizado_em", { withTimezone: true }).notNull().defaultNow(),
});

export const usuarios = pgTable("usuarios", {
  id: uuid("id").primaryKey().defaultRandom(),
  escolaId: uuid("escola_id").references(() => escolas.id), // nulo só para SUPER_ADMIN
  nome: text("nome").notNull(),
  email: text("email").notNull().unique(),
  senhaHash: text("senha_hash").notNull(),
  perfil: perfilUsuario("perfil").notNull(),
  ativo: boolean("ativo").notNull().default(true),
  totpSegredoCifrado: text("totp_segredo_cifrado"),
  tentativasFalhas: integer("tentativas_falhas").notNull().default(0),
  bloqueadoAte: timestamp("bloqueado_ate", { withTimezone: true }),
  ultimoLogin: timestamp("ultimo_login", { withTimezone: true }),
  criadoEm: criadoEm(),
}, (t) => [
  check("super_admin_sem_escola", sql`(${t.perfil} = 'SUPER_ADMIN') = (${t.escolaId} IS NULL)`),
]);

export const sessoes = pgTable("sessoes", {
  id: text("id").primaryKey(), // SHA-256 do token; o token puro só existe no cookie
  usuarioId: uuid("usuario_id").notNull().references(() => usuarios.id, { onDelete: "cascade" }),
  expiraEm: timestamp("expira_em", { withTimezone: true }).notNull(),
  ultimoUso: timestamp("ultimo_uso", { withTimezone: true }).notNull().defaultNow(),
  ip: text("ip"),
  userAgent: text("user_agent"),
});

export const turmas = pgTable("turmas", {
  id: uuid("id").primaryKey().defaultRandom(),
  escolaId: uuid("escola_id").notNull().references(() => escolas.id),
  nome: text("nome").notNull(),
  ordem: integer("ordem").notNull().default(0),
  ativa: boolean("ativa").notNull().default(true),
}, (t) => [uniqueIndex("turmas_escola_nome").on(t.escolaId, t.nome)]);

export const campanhas = pgTable("campanhas", {
  id: uuid("id").primaryKey().defaultRandom(),
  escolaId: uuid("escola_id").notNull().references(() => escolas.id),
  slug: text("slug").notNull(),
  nome: text("nome").notNull(),
  descricao: text("descricao"),
  textoApresentacao: text("texto_apresentacao"),
  imagemUrl: text("imagem_url"),
  dataInicio: timestamp("data_inicio", { withTimezone: true }).notNull(),
  dataFim: timestamp("data_fim", { withTimezone: true }),
  status: statusCampanha("status").notNull().default("RASCUNHO"),
  metaCentavos: integer("meta_centavos"),
  valorMinimoCentavos: integer("valor_minimo_centavos"),
  ordem: integer("ordem").notNull().default(0),
  criadoEm: criadoEm(),
}, (t) => [uniqueIndex("campanhas_escola_slug").on(t.escolaId, t.slug)]);

export const itens = pgTable("itens", {
  id: uuid("id").primaryKey().defaultRandom(),
  escolaId: uuid("escola_id").notNull().references(() => escolas.id),
  campanhaId: uuid("campanha_id").notNull().references(() => campanhas.id),
  tipo: tipoItem("tipo").notNull(),
  nome: text("nome").notNull(),
  descricao: text("descricao"),
  imagemUrl: text("imagem_url"),
  precoCentavos: integer("preco_centavos"), // nulo apenas em VALOR_LIVRE
  controlaEstoque: boolean("controla_estoque").notNull().default(false),
  estoqueTotal: integer("estoque_total"),
  estoqueReservado: integer("estoque_reservado").notNull().default(0),
  estoqueVendido: integer("estoque_vendido").notNull().default(0),
  limitePorPedido: integer("limite_por_pedido"),
  inicioVenda: timestamp("inicio_venda", { withTimezone: true }),
  fimVenda: timestamp("fim_venda", { withTimezone: true }),
  ativo: boolean("ativo").notNull().default(true),
  ordem: integer("ordem").notNull().default(0),
  criadoEm: criadoEm(),
}, (t) => [
  index("itens_escola_campanha").on(t.escolaId, t.campanhaId),
  check("preco_coerente", sql`(${t.tipo} = 'VALOR_LIVRE') = (${t.precoCentavos} IS NULL)`),
  check("preco_positivo", sql`${t.precoCentavos} IS NULL OR ${t.precoCentavos} > 0`),
  check("estoque_nao_negativo", sql`${t.estoqueReservado} >= 0 AND ${t.estoqueVendido} >= 0`),
  check("estoque_coerente", sql`NOT ${t.controlaEstoque} OR ${t.estoqueTotal} IS NOT NULL`),
]);

export const pedidos = pgTable("pedidos", {
  id: uuid("id").primaryKey().defaultRandom(),
  escolaId: uuid("escola_id").notNull().references(() => escolas.id),
  codigo: text("codigo").notNull(),
  tokenAcesso: text("token_acesso").notNull().unique(),
  responsavelNome: text("responsavel_nome"),
  responsavelEmail: text("responsavel_email"),
  responsavelTelefone: text("responsavel_telefone"),
  alunoNome: text("aluno_nome"),
  turmaId: uuid("turma_id").references(() => turmas.id),
  observacao: text("observacao"),
  anonimo: boolean("anonimo").notNull().default(false),
  totalCentavos: integer("total_centavos").notNull(),
  status: statusPedido("status").notNull().default("AGUARDANDO_PAGAMENTO"),
  statusEntrega: statusEntrega("status_entrega").notNull().default("NAO_SE_APLICA"),
  formaPagamento: formaPagamento("forma_pagamento").notNull().default("PIX"),
  estoqueLiberado: boolean("estoque_liberado").notNull().default(false),
  requerRevisao: boolean("requer_revisao").notNull().default(false),
  motivoRevisao: text("motivo_revisao"),
  expiraEm: timestamp("expira_em", { withTimezone: true }).notNull(),
  pagoEm: timestamp("pago_em", { withTimezone: true }),
  lancadoPorId: uuid("lancado_por_id").references(() => usuarios.id),
  criadoEm: criadoEm(),
}, (t) => [
  uniqueIndex("pedidos_escola_codigo").on(t.escolaId, t.codigo),
  index("pedidos_escola_status_data").on(t.escolaId, t.status, t.criadoEm),
  check("total_positivo", sql`${t.totalCentavos} > 0`),
]);

export const itensPedido = pgTable("itens_pedido", {
  id: uuid("id").primaryKey().defaultRandom(),
  escolaId: uuid("escola_id").notNull().references(() => escolas.id),
  pedidoId: uuid("pedido_id").notNull().references(() => pedidos.id),
  itemId: uuid("item_id").notNull().references(() => itens.id),
  campanhaId: uuid("campanha_id").notNull().references(() => campanhas.id),
  tipo: tipoItem("tipo").notNull(),
  descricao: text("descricao").notNull(),
  mesReferencia: text("mes_referencia"), // "2026-09" em CONTRIBUICAO_MENSAL
  quantidade: integer("quantidade").notNull(),
  valorUnitarioCentavos: integer("valor_unitario_centavos").notNull(),
  valorTotalCentavos: integer("valor_total_centavos").notNull(),
}, (t) => [
  index("itens_pedido_pedido").on(t.pedidoId),
  check("quantidade_positiva", sql`${t.quantidade} > 0`),
]);

export const pagamentos = pgTable("pagamentos", {
  id: uuid("id").primaryKey().defaultRandom(),
  escolaId: uuid("escola_id").notNull().references(() => escolas.id),
  pedidoId: uuid("pedido_id").notNull().references(() => pedidos.id),
  provedor: text("provedor").notNull(),
  txid: text("txid").notNull().unique(),
  endToEndId: text("end_to_end_id").unique(),
  valorCentavos: integer("valor_centavos").notNull(),
  valorPagoCentavos: integer("valor_pago_centavos"),
  pixCopiaECola: text("pix_copia_e_cola"),
  status: statusPagamento("status").notNull().default("CRIADO"),
  pagadorNomeCifrado: text("pagador_nome_cifrado"),
  erro: text("erro"),
  expiraEm: timestamp("expira_em", { withTimezone: true }).notNull(),
  pagoEm: timestamp("pago_em", { withTimezone: true }),
  criadoEm: criadoEm(),
}, (t) => [index("pagamentos_pedido").on(t.pedidoId)]);

export const eventosWebhook = pgTable("eventos_webhook", {
  id: uuid("id").primaryKey().defaultRandom(),
  escolaId: uuid("escola_id").references(() => escolas.id),
  provedor: text("provedor").notNull(),
  recebidoEm: timestamp("recebido_em", { withTimezone: true }).notNull().defaultNow(),
  ip: text("ip"),
  corpo: jsonb("corpo"),
  valido: boolean("valido").notNull(),
  processadoEm: timestamp("processado_em", { withTimezone: true }),
  erro: text("erro"),
});

export const pixAvulsos = pgTable("pix_avulsos", {
  id: uuid("id").primaryKey().defaultRandom(),
  escolaId: uuid("escola_id").notNull().references(() => escolas.id),
  endToEndId: text("end_to_end_id").notNull().unique(),
  valorCentavos: integer("valor_centavos").notNull(),
  horario: timestamp("horario", { withTimezone: true }).notNull(),
  infoPagador: text("info_pagador"),
  pedidoId: uuid("pedido_id").references(() => pedidos.id),
  criadoEm: criadoEm(),
});

export const logsAuditoria = pgTable("logs_auditoria", {
  id: uuid("id").primaryKey().defaultRandom(),
  escolaId: uuid("escola_id").references(() => escolas.id),
  usuarioId: uuid("usuario_id").references(() => usuarios.id),
  acao: text("acao").notNull(),
  entidade: text("entidade").notNull(),
  entidadeId: text("entidade_id").notNull(),
  antes: jsonb("antes"),
  depois: jsonb("depois"),
  ip: text("ip"),
  criadoEm: criadoEm(),
}, (t) => [index("logs_escola_data").on(t.escolaId, t.criadoEm)]);

export const notificacoes = pgTable("notificacoes", {
  id: uuid("id").primaryKey().defaultRandom(),
  escolaId: uuid("escola_id").notNull().references(() => escolas.id),
  evento: text("evento").notNull(),
  canal: text("canal").notNull(),
  destino: text("destino").notNull(),
  payload: jsonb("payload").notNull(),
  status: text("status").notNull().default("PENDENTE"),
  tentativas: integer("tentativas").notNull().default(0),
  criadoEm: criadoEm(),
});
