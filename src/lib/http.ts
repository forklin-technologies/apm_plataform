import { NextResponse } from "next/server";
import { ErroNegocio } from "./erros";
import { log } from "./log";

/** Converte erros em respostas seguras (sem vazar detalhes internos). */
export function responderErro(e: unknown) {
  if (e instanceof ErroNegocio) {
    return NextResponse.json({ erro: e.codigo, mensagem: e.message }, { status: e.status });
  }
  log.error({ err: e }, "erro inesperado");
  return NextResponse.json({ erro: "ERRO_INTERNO", mensagem: "Algo deu errado. Tente novamente." }, { status: 500 });
}

/** Rejeita POST vindo de outro site (quando o navegador informa a origem). */
export function origemPermitida(req: Request): boolean {
  const origem = req.headers.get("origin");
  if (!origem) return true;
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host");
  try { return new URL(origem).host === host; } catch { return false; }
}
