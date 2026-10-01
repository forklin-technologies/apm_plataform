/**
 * API Pix v2 do Banco do Brasil.
 *
 * Particularidades em relação ao padrão Bacen:
 * - Toda chamada leva a "developer application key" como parâmetro de query.
 *   O nome do parâmetro é configurável (BB_APP_KEY_PARAM) porque a
 *   documentação do BB usa "gw-dev-app-key" e "gw-app-key" conforme o ambiente.
 * - A v2 exige certificado digital (mTLS) cadastrado no Portal Developers BB.
 * - URLs padrão abaixo devem ser CONFERIDAS no Portal Developers BB no momento
 *   da homologação; podem ser sobrescritas por variáveis de ambiente.
 */
import { BacenPadraoProvider, type ConfigBacen, type Transporte } from "./bacen";

export const URLS_BB = {
  homologacao: {
    baseUrl: process.env.BB_PIX_URL_HOMOLOGACAO ?? "https://api.hm.bb.com.br/pix/v2",
    tokenUrl: process.env.BB_OAUTH_URL_HOMOLOGACAO ?? "https://oauth.hm.bb.com.br/oauth/token",
  },
  producao: {
    baseUrl: process.env.BB_PIX_URL_PRODUCAO ?? "https://api-pix.bb.com.br/pix/v2",
    tokenUrl: process.env.BB_OAUTH_URL_PRODUCAO ?? "https://oauth.bb.com.br/oauth/token",
  },
} as const;

export const ESCOPOS_BB = "cob.write cob.read pix.read pix.write webhook.read webhook.write";

export interface ConfigBB extends Omit<ConfigBacen, "baseUrl" | "tokenUrl" | "escopos"> {
  appKey: string;
  appKeyParam?: string;
}

export class BancoDoBrasilProvider extends BacenPadraoProvider {
  override readonly nome = "bb";
  private appKey: string;
  private appKeyParam: string;

  constructor(cfg: ConfigBB, transporte?: Transporte) {
    super({ ...cfg, ...URLS_BB[cfg.ambiente], escopos: process.env.BB_ESCOPOS ?? ESCOPOS_BB }, transporte);
    this.appKey = cfg.appKey;
    this.appKeyParam = cfg.appKeyParam ?? process.env.BB_APP_KEY_PARAM ?? "gw-dev-app-key";
  }

  protected override parametrosExtras() {
    return { [this.appKeyParam]: this.appKey };
  }
}
