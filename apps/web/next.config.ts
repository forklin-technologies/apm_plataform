import type { NextConfig } from "next";

// Destino do rewrite de /api/*. Em producao real quem faz isso e o proxy reverso (ADR-009).
// Valor lido na hora do build (o Next compila os rewrites no manifesto de rotas).
// Nunca use NEXT_PUBLIC_ aqui: o navegador so conhece /api/*.
function resolveApiProxyUrl(): string {
  const raw = process.env.API_PROXY_URL?.trim() || "http://127.0.0.1:8001";
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("API_PROXY_URL invalida: informe uma URL http(s) completa.");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("API_PROXY_URL invalida: use http ou https.");
  }
  return url.origin;
}

const apiProxyUrl = resolveApiProxyUrl();

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  // O `next dev` gera AGENTS.md/CLAUDE.md sozinho; fora do escopo desta tarefa.
  agentRules: false,
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiProxyUrl}/api/:path*` }];
  },
  async headers() {
    // A CSP (com nonce por requisicao) e montada em src/proxy.ts.
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
