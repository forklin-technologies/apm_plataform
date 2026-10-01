/**
 * Processo de rotinas. Rodar como serviço separado da aplicação web:
 *   npm run jobs
 * - a cada minuto: verifica cobranças pendentes e expira pedidos vencidos
 * - todo dia às 06:00: concilia os Pix do dia anterior de todas as escolas
 */
import { verificarPendentes, conciliarTodasEscolas } from "@/services/rotinas";
import { log } from "@/lib/log";

let ocupado = false;
async function aCadaMinuto() {
  if (ocupado) return;
  ocupado = true;
  try {
    const r = await verificarPendentes();
    if (r.confirmados || r.expirados || r.erros) log.info(r, "verificação de pendentes");
    const agora = new Date();
    if (agora.getHours() === 6 && agora.getMinutes() === 0) {
      const fim = new Date(agora); fim.setHours(0, 0, 0, 0);
      const inicio = new Date(fim.getTime() - 24 * 3600 * 1000);
      await conciliarTodasEscolas(inicio, fim);
    }
  } catch (e) { log.error({ err: e }, "falha na rotina"); }
  finally { ocupado = false; }
}

log.info("rotinas iniciadas");
aCadaMinuto();
setInterval(aCadaMinuto, 60_000);
