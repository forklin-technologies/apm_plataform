/**
 * Provedor Pix no padrão da API Pix do Banco Central (v2).
 * Referência: https://bacen.github.io/pix-api/
 *
 * Implementa OAuth2 client_credentials + mTLS (certificado .p12/.pfx) e os
 * endpoints /cob, /pix, /pix/{e2eid}/devolucao e /webhook/{chave}.
 * Bancos específicos (ex.: Banco do Brasil) estendem esta classe.
 */
import { Agent, fetch as undiciFetch } from "undici";
import { z } from "zod";
import { centavosParaDecimal, decimalParaCentavos } from "@/lib/dinheiro";
import { ErroProvedorPix } from "@/lib/erros";
import type {
  CobrancaCriada, ConsultaCobranca, NovaCobranca, PixProvider, PixRecebido, StatusCobranca,
} from "./tipos";

export interface ConfigBacen {
  ambiente: "homologacao" | "producao";
  baseUrl: string;          // ex.: https://api.banco.com.br/pix/v2
  tokenUrl: string;         // ex.: https://oauth.banco.com.br/oauth/token
  clientId: string;
  clientSecret: string;
  chavePix: string;
  escopos: string;
  certificado?: { pfx: Buffer; senha?: string };
}

/** Função de transporte HTTP; substituível nos testes. */
export type Transporte = (url: string, init: {
  method: string; headers: Record<string, string>; body?: string;
}) => Promise<{ status: number; texto: string }>;

function transporteUndici(cert?: { pfx: Buffer; senha?: string }): Transporte {
  const dispatcher = cert ? new Agent({ connect: { pfx: cert.pfx, passphrase: cert.senha } }) : undefined;
  return async (url, init) => {
    const r = await undiciFetch(url, { ...init, dispatcher, signal: AbortSignal.timeout(15000) });
    return { status: r.status, texto: await r.text() };
  };
}

const pixSchema = z.object({
  endToEndId: z.string().min(1),
  txid: z.string().optional(),
  valor: z.string(),
  horario: z.string(),
  infoPagador: z.string().optional(),
  pagador: z.object({ nome: z.string().optional() }).passthrough().optional(),
}).passthrough();

const cobSchema = z.object({
  txid: z.string(),
  status: z.string(),
  valor: z.object({ original: z.string() }).passthrough(),
  pixCopiaECola: z.string().optional(),
  pix: z.array(pixSchema).optional(),
}).passthrough();

const listaPixSchema = z.object({
  pix: z.array(pixSchema).optional().default([]),
  parametros: z.object({
    paginacao: z.object({ paginaAtual: z.number(), quantidadeDePaginas: z.number() }).passthrough(),
  }).passthrough().optional(),
}).passthrough();

const webhookSchema = z.object({ pix: z.array(pixSchema).min(1) }).passthrough();

function mapearStatus(s: string): StatusCobranca {
  if (s === "ATIVA") return "ATIVA";
  if (s === "CONCLUIDA") return "CONCLUIDA";
  if (s.startsWith("REMOVIDA")) return "REMOVIDA";
  if (s === "EXPIRADA") return "EXPIRADA";
  throw new ErroProvedorPix(`Status de cobrança desconhecido: ${s}`);
}

function mapearPix(p: z.infer<typeof pixSchema>): PixRecebido {
  return {
    endToEndId: p.endToEndId,
    txid: p.txid,
    valorCentavos: decimalParaCentavos(p.valor),
    horario: new Date(p.horario),
    infoPagador: p.infoPagador,
    pagadorNome: p.pagador?.nome,
  };
}

export class BacenPadraoProvider implements PixProvider {
  readonly nome: string = "bacen";
  private token?: { valor: string; expiraEm: number };
  protected transporte: Transporte;

  constructor(protected cfg: ConfigBacen, transporte?: Transporte) {
    this.transporte = transporte ?? transporteUndici(cfg.certificado);
  }

  get ambiente() { return this.cfg.ambiente; }

  /** Parâmetros extras de query exigidos por alguns bancos (ex.: chave de app do BB). */
  protected parametrosExtras(): Record<string, string> { return {}; }

  protected url(caminho: string, query: Record<string, string> = {}): string {
    const u = new URL(this.cfg.baseUrl.replace(/\/$/, "") + caminho);
    for (const [k, v] of Object.entries({ ...query, ...this.parametrosExtras() })) u.searchParams.set(k, v);
    return u.toString();
  }

  protected async obterToken(): Promise<string> {
    if (this.token && this.token.expiraEm > Date.now() + 30_000) return this.token.valor;
    const basic = Buffer.from(`${this.cfg.clientId}:${this.cfg.clientSecret}`).toString("base64");
    const r = await this.transporte(this.cfg.tokenUrl, {
      method: "POST",
      headers: { Authorization: `Basic ${basic}`, "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "client_credentials", scope: this.cfg.escopos }).toString(),
    });
    if (r.status !== 200) throw new ErroProvedorPix("Falha na autenticação com o banco", r.status, r.texto.slice(0, 500));
    const j = z.object({ access_token: z.string(), expires_in: z.coerce.number().default(600) }).parse(JSON.parse(r.texto));
    this.token = { valor: j.access_token, expiraEm: Date.now() + j.expires_in * 1000 };
    return j.access_token;
  }

  protected async chamar(metodo: string, caminho: string, opts: { query?: Record<string, string>; corpo?: unknown } = {}) {
    const exec = async () => this.transporte(this.url(caminho, opts.query), {
      method: metodo,
      headers: {
        Authorization: `Bearer ${await this.obterToken()}`,
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: opts.corpo === undefined ? undefined : JSON.stringify(opts.corpo),
    });
    let r = await exec();
    if (r.status === 401) { this.token = undefined; r = await exec(); } // token revogado: tenta uma vez
    if (r.status < 200 || r.status >= 300) {
      throw new ErroProvedorPix(`Banco respondeu ${r.status} em ${metodo} ${caminho}`, r.status, r.texto.slice(0, 1000));
    }
    return r.texto ? JSON.parse(r.texto) : {};
  }

  async criarCobranca(c: NovaCobranca): Promise<CobrancaCriada> {
    const corpo = {
      calendario: { expiracao: c.expiracaoSegundos },
      valor: { original: centavosParaDecimal(c.valorCentavos), modalidadeAlteracao: 0 },
      chave: this.cfg.chavePix,
      solicitacaoPagador: c.solicitacaoPagador.slice(0, 140),
      infoAdicionais: c.infoAdicionais?.map((i) => ({ nome: i.nome.slice(0, 50), valor: i.valor.slice(0, 200) })),
    };
    const resp = cobSchema.parse(await this.chamar("PUT", `/cob/${c.txid}`, { corpo }));
    if (decimalParaCentavos(resp.valor.original) !== c.valorCentavos) {
      throw new ErroProvedorPix("Banco devolveu cobrança com valor diferente do solicitado");
    }
    if (!resp.pixCopiaECola) throw new ErroProvedorPix("Banco não devolveu o código Pix copia e cola");
    return { txid: resp.txid, pixCopiaECola: resp.pixCopiaECola, status: mapearStatus(resp.status) };
  }

  async consultarCobranca(txid: string): Promise<ConsultaCobranca> {
    const resp = cobSchema.parse(await this.chamar("GET", `/cob/${txid}`));
    return {
      txid: resp.txid,
      status: mapearStatus(resp.status),
      valorCentavos: decimalParaCentavos(resp.valor.original),
      pagamentos: (resp.pix ?? []).map(mapearPix),
    };
  }

  async listarPixRecebidos(inicio: Date, fim: Date): Promise<PixRecebido[]> {
    const todos: PixRecebido[] = [];
    for (let pagina = 0; pagina < 100; pagina++) {
      const resp = listaPixSchema.parse(await this.chamar("GET", "/pix", {
        query: {
          inicio: inicio.toISOString(), fim: fim.toISOString(),
          "paginacao.paginaAtual": String(pagina), "paginacao.itensPorPagina": "100",
        },
      }));
      todos.push(...resp.pix.map(mapearPix));
      const pag = resp.parametros?.paginacao;
      if (!pag || pag.paginaAtual + 1 >= pag.quantidadeDePaginas) break;
    }
    return todos;
  }

  async devolver(endToEndId: string, idDevolucao: string, valorCentavos: number): Promise<void> {
    await this.chamar("PUT", `/pix/${endToEndId}/devolucao/${idDevolucao}`, {
      corpo: { valor: centavosParaDecimal(valorCentavos) },
    });
  }

  async registrarWebhook(url: string): Promise<void> {
    await this.chamar("PUT", `/webhook/${encodeURIComponent(this.cfg.chavePix)}`, { corpo: { webhookUrl: url } });
  }

  interpretarWebhook(corpo: unknown) {
    const r = webhookSchema.safeParse(corpo);
    if (!r.success) return [];
    return r.data.pix.map((p) => ({ txid: p.txid, endToEndId: p.endToEndId }));
  }
}
