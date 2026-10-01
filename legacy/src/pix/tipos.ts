/**
 * Contrato que todo provedor Pix implementa. O resto do sistema só conhece
 * esta interface — trocar de banco não exige mudar regras de negócio.
 */

export type StatusCobranca = "ATIVA" | "CONCLUIDA" | "REMOVIDA" | "EXPIRADA";

export interface PixRecebido {
  endToEndId: string;
  txid?: string;
  valorCentavos: number;
  horario: Date;
  infoPagador?: string;
  pagadorNome?: string;
}

export interface NovaCobranca {
  txid: string;
  valorCentavos: number;
  expiracaoSegundos: number;
  /** Texto exibido no app do banco de quem paga (máx. 140). */
  solicitacaoPagador: string;
  /** Pares exibidos no app (ex.: número do pedido). */
  infoAdicionais?: { nome: string; valor: string }[];
}

export interface CobrancaCriada {
  txid: string;
  pixCopiaECola: string;
  status: StatusCobranca;
}

export interface ConsultaCobranca {
  txid: string;
  status: StatusCobranca;
  valorCentavos: number;
  pagamentos: PixRecebido[];
}

export interface PixProvider {
  readonly nome: string;
  readonly ambiente: "homologacao" | "producao" | "sandbox";
  criarCobranca(c: NovaCobranca): Promise<CobrancaCriada>;
  consultarCobranca(txid: string): Promise<ConsultaCobranca>;
  listarPixRecebidos(inicio: Date, fim: Date): Promise<PixRecebido[]>;
  devolver(endToEndId: string, idDevolucao: string, valorCentavos: number): Promise<void>;
  registrarWebhook(url: string): Promise<void>;
  /** Extrai as notificações do corpo do webhook. Nunca confirma pagamento sozinho. */
  interpretarWebhook(corpo: unknown): { txid?: string; endToEndId: string }[];
}
