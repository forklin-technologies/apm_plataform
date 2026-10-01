/**
 * Provedor de TESTE. Existe apenas para desenvolvimento e testes automatizados.
 *
 * - Recusa-se a funcionar com NODE_ENV=production.
 * - O "copia e cola" gerado NÃO é um BR Code válido: não pode ser pago em
 *   nenhum banco, evitando que alguém transfira dinheiro de verdade por engano.
 * - Pagamentos só acontecem via `simularPagamento` (rota /api/sandbox/pagar).
 */
import { randomBytes } from "node:crypto";
import { ErroProvedorPix } from "@/lib/erros";
import type { ConsultaCobranca, NovaCobranca, PixProvider, PixRecebido } from "./tipos";

interface CobSandbox { txid: string; valorCentavos: number; criadaEm: number; expiracao: number; pagamentos: PixRecebido[]; removida: boolean }

const g = globalThis as unknown as { __sandboxPix?: Map<string, CobSandbox>; __sandboxWebhooks?: Map<string, string> };
const cobs = (g.__sandboxPix ??= new Map());

export function sandboxPermitido(): boolean {
  return process.env.NODE_ENV !== "production";
}

export class SandboxProvider implements PixProvider {
  readonly nome = "sandbox";
  readonly ambiente = "sandbox" as const;

  constructor(private escolaId: string) {
    if (!sandboxPermitido()) throw new Error("Provedor sandbox não pode ser usado em produção");
  }

  async criarCobranca(c: NovaCobranca) {
    if (cobs.has(c.txid)) throw new ErroProvedorPix("txid já utilizado", 409);
    cobs.set(c.txid, { txid: c.txid, valorCentavos: c.valorCentavos, criadaEm: Date.now(), expiracao: c.expiracaoSegundos, pagamentos: [], removida: false });
    return { txid: c.txid, status: "ATIVA" as const, pixCopiaECola: `SANDBOX-NAO-PAGAVEL|${c.txid}|${c.valorCentavos}` };
  }

  async consultarCobranca(txid: string): Promise<ConsultaCobranca> {
    const c = cobs.get(txid);
    if (!c) throw new ErroProvedorPix("Cobrança não encontrada", 404);
    const expirada = Date.now() > c.criadaEm + c.expiracao * 1000;
    const status = c.pagamentos.length ? "CONCLUIDA" : c.removida ? "REMOVIDA" : expirada ? "EXPIRADA" : "ATIVA";
    return { txid, status, valorCentavos: c.valorCentavos, pagamentos: [...c.pagamentos] };
  }

  async listarPixRecebidos(inicio: Date, fim: Date) {
    return [...cobs.values()].flatMap((c) => c.pagamentos).filter((p) => p.horario >= inicio && p.horario <= fim);
  }

  async devolver() { /* sem efeito no sandbox */ }
  async registrarWebhook() { /* sem efeito no sandbox */ }

  interpretarWebhook(corpo: unknown) {
    const pix = (corpo as { pix?: { txid?: string; endToEndId: string }[] })?.pix;
    return Array.isArray(pix) ? pix.map((p) => ({ txid: p.txid, endToEndId: p.endToEndId })) : [];
  }

  /** Simula o pagamento pelo app do banco. `valorCentavos` permite testar divergência. */
  static simularPagamento(txid: string, valorCentavos?: number, pagadorNome = "Pagador de Teste"): PixRecebido {
    if (!sandboxPermitido()) throw new Error("Indisponível em produção");
    const c = cobs.get(txid);
    if (!c) throw new Error("Cobrança sandbox não encontrada");
    const p: PixRecebido = {
      endToEndId: "E" + randomBytes(15).toString("hex").slice(0, 31).toUpperCase(),
      txid, valorCentavos: valorCentavos ?? c.valorCentavos, horario: new Date(), pagadorNome,
    };
    c.pagamentos.push(p);
    return p;
  }

  static limpar() { cobs.clear(); }
}
