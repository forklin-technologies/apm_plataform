/**
 * Token CSRF (docs/auth.md): a API o grava num cookie LEGIVEL pela pagina e espera o mesmo valor
 * no cabecalho X-CSRF-Token em todo pedido que muda dados. O cookie e a unica fonte: ele acompanha
 * a sessao certa mesmo depois de trocar de contexto em outra aba ou de trocar a senha.
 * Em producao o nome ganha o prefixo __Host-. O cookie de SESSAO e HttpOnly: o site nunca o le.
 */
const CSRF_COOKIE_NAMES = ["__Host-apm_csrf", "apm_csrf"] as const;
const TOKEN_SHAPE = /^[A-Za-z0-9._~-]{16,512}$/;

export function readCsrfToken(cookieString?: string): string | null {
  const source = cookieString ?? (typeof document === "undefined" ? "" : document.cookie);
  const jar = new Map<string, string>();
  for (const part of source.split(";")) {
    const index = part.indexOf("=");
    if (index > 0) jar.set(part.slice(0, index).trim(), part.slice(index + 1).trim());
  }
  for (const name of CSRF_COOKIE_NAMES) {
    const value = jar.get(name);
    if (value && TOKEN_SHAPE.test(value)) return value;
  }
  return null;
}
