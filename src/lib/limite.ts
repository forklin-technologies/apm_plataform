/**
 * Limitador de requisições (janela deslizante simples, em memória).
 * Suficiente para uma instância. Com várias instâncias, trocar por Redis ou
 * por limite no proxy — a interface continua a mesma.
 */
const baldes = new Map<string, number[]>();

export function permitir(chave: string, limite: number, janelaMs: number): boolean {
  const agora = Date.now();
  const lista = (baldes.get(chave) ?? []).filter((t) => t > agora - janelaMs);
  if (lista.length >= limite) { baldes.set(chave, lista); return false; }
  lista.push(agora);
  baldes.set(chave, lista);
  if (baldes.size > 50_000) baldes.clear(); // proteção de memória
  return true;
}

export function ipDaRequisicao(req: Request): string {
  // O proxy reverso deve sobrescrever (não acrescentar) este cabeçalho.
  return req.headers.get("x-real-ip") ?? req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "desconhecido";
}
