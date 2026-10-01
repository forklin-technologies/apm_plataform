/**
 * Monta o provedor Pix de uma escola a partir das credenciais cifradas no banco.
 */
import { eq } from "drizzle-orm";
import type { Tx } from "@/db/cliente";
import { credenciaisPix } from "@/db/schema";
import { decifrar, decifrarBytes } from "@/lib/cripto";
import { ErroNegocio } from "@/lib/erros";
import { BancoDoBrasilProvider } from "./bancoDoBrasil";
import { SandboxProvider, sandboxPermitido } from "./sandbox";
import type { PixProvider } from "./tipos";

type Credencial = typeof credenciaisPix.$inferSelect;

export function montarProvedor(c: Credencial): PixProvider {
  switch (c.provedor) {
    case "sandbox":
      if (!sandboxPermitido()) {
        // Proteção: uma escola com credencial de teste nunca recebe pedidos em produção.
        throw new ErroNegocio("PIX_NAO_CONFIGURADO", "A escola ainda não configurou o recebimento por Pix.", 503);
      }
      return new SandboxProvider(c.escolaId);
    case "bb": {
      if (c.ambiente !== "homologacao" && c.ambiente !== "producao") throw new Error("Ambiente inválido");
      if (!c.appKeyCifrada) throw new Error("Chave de aplicação do BB ausente");
      return new BancoDoBrasilProvider({
        ambiente: c.ambiente,
        clientId: decifrar(c.clientIdCifrado),
        clientSecret: decifrar(c.clientSecretCifrado),
        appKey: decifrar(c.appKeyCifrada),
        chavePix: c.chavePix,
        certificado: c.certificadoCifrado
          ? { pfx: decifrarBytes(c.certificadoCifrado.toString("utf8")),
              senha: c.senhaCertificadoCifrada ? decifrar(c.senhaCertificadoCifrada) : undefined }
          : undefined,
      });
    }
    default:
      throw new Error(`Provedor Pix não suportado: ${c.provedor}`);
  }
}

// Cache por escola (reaproveita token OAuth). Invalidado quando a credencial muda.
const cache = new Map<string, { atualizadoEm: number; provedor: PixProvider }>();

/** Permite aos testes injetar um provedor. */
export const provedoresInjetados = new Map<string, PixProvider>();

export async function provedorDaEscola(tx: Tx, escolaId: string): Promise<PixProvider> {
  const injetado = provedoresInjetados.get(escolaId);
  if (injetado) return injetado;
  const [c] = await tx.select().from(credenciaisPix).where(eq(credenciaisPix.escolaId, escolaId));
  if (!c) throw new ErroNegocio("PIX_NAO_CONFIGURADO", "A escola ainda não configurou o recebimento por Pix.", 503);
  const emCache = cache.get(escolaId);
  if (emCache && emCache.atualizadoEm === c.atualizadoEm.getTime()) return emCache.provedor;
  const provedor = montarProvedor(c);
  cache.set(escolaId, { atualizadoEm: c.atualizadoEm.getTime(), provedor });
  return provedor;
}
