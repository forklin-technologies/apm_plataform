# apps/web — APM Digital (interface)

Next.js (App Router) + TypeScript strict + Tailwind CSS v4. Só UI: nenhuma regra financeira mora
aqui. O frontend **nunca decide que um Pix foi pago**; ele mostra o status que a camada de dados
devolve.

## Rodar

```bash
cd apps/web
cp .env.example .env.local   # opcional: só tem API_PROXY_URL
npm ci
npm run dev                  # http://127.0.0.1:3101
```

Produção local (a "prévia"):

```bash
npm run build
npm run start -- -p 3100 -H 127.0.0.1
```

| Script | O que faz |
|---|---|
| `npm run dev` | servidor de desenvolvimento em 127.0.0.1:3101 |
| `npm run build` / `start` | build e servidor de produção |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | Vitest + Testing Library |

## Variáveis de ambiente

- `API_PROXY_URL` (padrão `http://127.0.0.1:8001`): destino do rewrite de `/api/*`. É lida **no
  build** (o Next compila os rewrites). Sem segredos, e nunca `NEXT_PUBLIC_*`.

## Dados: o que é real e o que é simulado

- **Real**: `GET /api/health/ready` (chip de status da API na entrada e no painel). O navegador só
  chama `/api/*` na mesma origem; não há CORS nem requisição a terceiros.
- **Simulado**: escolas, contribuição, Pix, comprovante e painel, em `src/mocks/`, atrás das
  interfaces de `src/lib/api/types.ts`. Toda tela mostra o marcador **"Protótipo · dados de
  exemplo"**. O Pix e o QR são de exemplo e não podem ser lidos por app de banco.
- Os endpoints que o frontend vai precisar estão em `docs/web-contract-proposals.md` como
  **proposta**.

Rotas: `/` · `/apm/[slug]` · `/apm/[slug]/pedido/[token]` · `/login` (visual, sem login) · `/painel`.
Escolas de exemplo: `escola-exemplo`, `escola-horizonte`, `emei-vale-verde`.

## Estrutura

```
src/app/          rotas (App Router)
src/components/   ui/, portal/, painel/, status/, login/
src/lib/          money (centavos + BRL), validation, status, color (contraste AA), api/, hooks/
src/mocks/        camada de dados simulada (só src/lib/api importa daqui)
src/proxy.ts      CSP com nonce por requisição
```

## Segurança

- CSP restritiva por requisição (`src/proxy.ts`): scripts só com nonce, `frame-ancestors 'none'`,
  `object-src 'none'`, sem origens de terceiros. Exceção consciente: `style-src-attr 'unsafe-inline'`,
  porque a cor da escola é aplicada como variáveis CSS num atributo `style` (renderizado no servidor).
- `X-Content-Type-Options`, `Referrer-Policy: no-referrer`, `X-Frame-Options`, `Permissions-Policy`
  e `poweredByHeader` desligado (`next.config.ts`). HSTS fica com o proxy reverso (HTTPS).
- Sem `dangerouslySetInnerHTML` (regra de lint). Sem segredos e sem `NEXT_PUBLIC_*`.
- Em produção, o proxy reverso não deve sobrescrever o `Content-Security-Policy` das páginas.

## Acessibilidade e movimento

Foco visível, alvos de toque de 44px, rótulos e `aria-live` nas mudanças de status, contraste AA
em claro e escuro (a cor da escola é recalculada em `src/lib/color.ts`) e `prefers-reduced-motion`
respeitado. O único momento orquestrado é o carimbo "Pago", quando a camada de dados devolve `PAID`.
