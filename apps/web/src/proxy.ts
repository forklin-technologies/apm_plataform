import { NextResponse, type NextRequest } from "next/server";

/**
 * CSP restritiva com nonce por requisicao (guia de CSP do Next).
 * - scripts so com nonce + strict-dynamic; nada de 'unsafe-inline' em script;
 * - 'unsafe-eval' somente em desenvolvimento (React usa eval para stack traces);
 * - style-src-attr 'unsafe-inline': necessario porque o tema da escola e aplicado como
 *   variaveis CSS no atributo style (SSR). Folhas de estilo e <style> continuam travados;
 * - nenhuma origem de terceiros: connect/img/font/default so 'self'.
 * Os demais cabecalhos de seguranca ficam em next.config.ts.
 */
export function proxy(request: NextRequest) {
  const nonce = btoa(crypto.randomUUID());
  const isDev = process.env.NODE_ENV === "development";

  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    // Em dev o overlay do Next injeta <style> sem nonce; em producao nao existe.
    isDev ? "style-src 'self' 'unsafe-inline'" : `style-src 'self' 'nonce-${nonce}'`,
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    `connect-src 'self'${isDev ? " ws:" : ""}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      // Delimitadores explicitos: "api" so exclui /api e /api/*, nunca /apix, /api-docs ou /apiary.
      source: "/((?!api$|api/|_next/static|_next/image|favicon\\.ico|icon\\.svg).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
