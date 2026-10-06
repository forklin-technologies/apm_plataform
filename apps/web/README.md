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
npm run start -- -p 3100     # o script start já embute -H 127.0.0.1: não abre a rede sozinho
```

Para expor de propósito (por exemplo, dentro de um container atrás do proxy reverso), passe o host
no comando: `npm run start -- -H 0.0.0.0 -p 3000`. O último `-H` vence.

| Script | O que faz |
|---|---|
| `npm run dev` | servidor de desenvolvimento em 127.0.0.1:3101 |
| `npm run build` / `start` | build e servidor de produção (`start` só liga em 127.0.0.1) |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | Vitest + Testing Library (inclui os guardas de CSS/animação) |
| `npm run test:e2e` | Playwright em WebKit e Chromium (precisa de `npx playwright install webkit chromium` uma vez) |

## Variáveis de ambiente

- `API_PROXY_URL` (padrão `http://127.0.0.1:8001`): destino do rewrite de `/api/*`. É lida **no
  build** (o Next compila os rewrites). Sem segredos, e nunca `NEXT_PUBLIC_*`.

## Dados: o que é real e o que é simulado

- **Real**: `GET /api/health/ready` (chip de status da API) e a **autenticação** (`src/lib/api/auth.ts`,
  contrato em `docs/auth.md`): `POST /api/v1/auth/login`, `logout`, `GET /auth/me`, `POST /auth/context`,
  `POST /auth/password` e `POST /api/v1/invitations/accept`. O navegador só chama `/api/*` na mesma
  origem; não há CORS nem requisição a terceiros.
  - A sessão é um cookie **HttpOnly** (`apm_session`; `__Host-apm_session` em produção) que o site nunca
    lê. O token CSRF vem do cookie legível `apm_csrf` e vai em `X-CSRF-Token` em todo pedido que muda
    dados. A API só aceita esses pedidos das origens locais `127.0.0.1:3101`/`localhost:3101` (dev) e
    `:3100` (prévia).
  - `/painel` exige sessão: o **servidor do Next** repassa o cookie a `GET /api/v1/auth/me`
    (`src/lib/api/server.ts`); sem sessão, redireciona para `/login`. API fora do ar não é logout: mostra
    "Não foi possível abrir o painel".
  - O seletor do painel lista os **vínculos reais** da pessoa e troca por `POST /auth/context`; o cliente
    nunca define organização nem escola. Sessão sem vínculo ativo mostra a escolha antes do painel.
  - Os textos de erro vêm do `code` do `problem+json` (`src/lib/auth-messages.ts`); o `title`/`detail` do
    servidor nunca aparece. `/login?next=` só aceita `/painel` e o link do convite (lista fechada).
  - `POST /auth/password` tem cliente e testes, mas **ainda não tem tela**.
- **Portal público (REAL)**, `/escola/{slug}` (`/apm/{slug}` redireciona), contrato em `docs/public-flow.md`
  (`src/lib/api/public.ts`): escola, campos de identificação (`REQUIRED`/`OPTIONAL`/`HIDDEN`), criação da
  contribuição com `Idempotency-Key` (uma `crypto.randomUUID()` por tentativa, só em memória e no cabeçalho),
  QR Code desenhado no navegador a partir de `emv_payload` (`qrcode` 1.5.4, sem chamada externa), polling de 2 s
  com recuo, `PAID`/`REVIEW_REQUIRED`/QR expirado (novo QR) e comprovante em `/escola/{slug}/pedido/{token}`.
  O site só mostra "pago" quando a **contribuição** vem `PAID` da API. Com payload `PIX-SANDBOX:` (só em
  desenvolvimento) aparece "Simular pagamento", que chama `POST /api/v1/dev/sandbox/pix/{txid}/pay`.
- **Painel (REAL)**, `docs/statement.md` (`src/lib/api/statement.ts`): para a escola do vínculo ativo
  (`active_membership.school.id`), resumo do mês com os dois saldos (em caixa, e após reembolsos pendentes),
  lançamentos por tipo com "carregar mais" (cursor), pendências fora do saldo, mês na URL (`?mes=YYYY-MM`),
  "Registrar contribuição em dinheiro" (`contributions:record_cash`) e link do PDF das contribuições do mês.
  Rota que responder 403 (o `viewer` só lê o resumo) vira aviso. Vínculo da organização inteira não tem painel
  de escola. Quem tem 2 ou mais vínculos escolhe o contexto como antes.
- **Simulado**: nada. Não há mais camada de mocks nem selo de protótipo. Ainda **sem tela**: despesas
  (professor e fila de análise), fechamento e PDF mensal, administração, troca de senha.

Usuários de demonstração (dados falsos) e a senha do seed ficam fora do repositório. Testes de unidade
usam mocks; o e2e com login real lê a senha só em tempo de execução (`E2E_DEMO_PASSWORD_FILE`, veja
`e2e/auth-helpers.ts`) e roda contra o `npm run dev` (3101), porque a API só aceita as origens acima.

Rotas: `/` · `/escola/[slug]` · `/escola/[slug]/pedido/[token]` · `/login` · `/accept-invitation?token=…` · `/painel` (exige sessão).
Escolas de demonstração (banco local): `demo-aurora`, `demo-horizonte`, `demo-central`.

## Estrutura

```
src/app/          rotas (App Router)
src/components/   ui/, portal/, painel/, status/, login/, invitation/
src/lib/          money (centavos + BRL), validation, status, color (contraste AA), api/ (health, auth, csrf, server), auth-messages, safe-next, roles, hooks/
src/proxy.ts      CSP com nonce por requisição
```

## Segurança

- CSP restritiva por requisição (`src/proxy.ts`): scripts só com nonce, `frame-ancestors 'none'`,
  `object-src 'none'`, sem origens de terceiros. Exceção consciente: `style-src-attr 'unsafe-inline'`,
  porque a cor da escola é aplicada como variáveis CSS num atributo `style` (renderizado no servidor).
- `X-Content-Type-Options`, `Referrer-Policy: no-referrer`, `X-Frame-Options`, `Permissions-Policy`
  e `poweredByHeader` desligado (`next.config.ts`). HSTS fica com o proxy reverso (HTTPS).
- O matcher do `proxy.ts` exclui só `/api`, `/api/*` e os estáticos: `/apix`, `/api-docs` e `/apiary`
  recebem a CSP com nonce (testado em unidade e em e2e).
- Páginas de erro (`error.tsx`, `global-error.tsx`) em português, sem expor `error.message`.
- Sem `dangerouslySetInnerHTML` (regra de lint). Sem segredos e sem `NEXT_PUBLIC_*`.
- Em produção, o proxy reverso não deve sobrescrever o `Content-Security-Policy` das páginas.

## Comprovante e privacidade (dado de criança)

- **O comprovante só afirma o que a API confirma.** O servidor do Next consulta `GET .../receipt`: `200` entrega o
  comprovante; `409` mostra "ainda não confirmado"; o `404` único da API (token inexistente, de outra escola ou
  malformado) vira a 404 do site; qualquer outra falha (429, API fora do ar) deixa o HTML **neutro** ("Conferindo seu
  comprovante": sem "Pago", sem nome, sem valor) e o navegador tenta de novo.
- O token da contribuição (segredo da família) só vive na memória da página do fluxo e na URL do comprovante; a
  `Idempotency-Key` é tão secreta quanto ele e só vai no cabeçalho do POST. Nada vai para `localStorage`,
  `sessionStorage`, URL de outra página nem log. O navegador não guarda dado pessoal.
- A API conta consultas que erram (token ou slug inexistente) contra o endereço e responde `429` com `Retry-After`;
  o site mostra um aviso com o tempo de espera (no polling, espera e tenta de novo). Rodar muitos e2e seguidos
  contra a mesma API pode bloquear o endereço por alguns minutos.
- Nenhum `<form>` cai em GET com dados na URL (todos `method="post"` com `preventDefault`); campos de nome
  sem corretor ortográfico (`spellcheck=false`) e aluno/turma sem autopreenchimento.
- Colar no campo de valor interpreta **reais** (`1.000` = R$ 1.000,00, `1,50` = R$ 1,50); o que não é
  valor é ignorado com um aviso curto. Digitar continua com a máscara de caixa eletrônico.

## Animações são melhoria progressiva (WebKit, portal do Maestri)

O estado base de **todo** conteúdo é visível. O WebKit **congela a linha do tempo das animações** com o
documento oculto (aba em segundo plano, portal do Maestri, WKWebView fora da tela): uma animação de
entrada parada em `t=0` mantém o keyframe inicial para sempre, e `opacity: 0` vira conteúdo invisível
(foi o defeito da rodada 1). Por isso:

- nenhum `@keyframes` parte de `opacity: 0` (só `transform` ou traçado decorativo);
- toda animação de entrada só existe sob `[data-motion="on"]`, que o `MotionGate` liga **apenas** com o
  documento visível; oculto, o conteúdo aparece direto no estado final;
- as animações de passo só rodam depois de uma ação do usuário, nunca no carregamento da página;
- o carimbo "Pago" entra por escala (nunca por `stroke-dashoffset`): congelado em qualquer ponto, o símbolo
  continua visível;
- o polling do chip da API não para com o documento oculto, só fica mais lento (60 s).

Guardas (Vitest): `src/app/motion-css.test.ts` + `src/test-utils/css-guard.ts` (opacity 0/0%, visibility,
escala < 0,1, translate fora da tela, clip-path, content-visibility e tamanho 0, no keyframe inicial e no
estado base, com testes de mutação), `src/hidden-content-sources.test.ts` (utilitários Tailwind que
escondem e o `MotionGate` no layout). Guardas (e2e, **com a CSP real ativa**, sem `bypassCSP`):
`e2e/visible-content.spec.ts` em WebKit e Chromium nos cenários `normal`, `frozen` (toda `CSSAnimation`
pausada em t=0 por Web Animations) e `hidden` (documento oculto + linha do tempo congelada, como o portal
do Maestri; exige `data-motion` desligado e zero animação de entrada). Falha se algum texto visível ficar
com opacidade efetiva < 0,9, se o carimbo SVG (anel/check) sumir ou se houver violação de CSP.
`e2e/detectors.spec.ts` testa os próprios detectores.

Observação para quem testa pelo portal do Maestri: a CSP de produção não permite `eval`, então
`maestri portal evaluate` não funciona na prévia (3100); `snapshot`, `click`, `fill` e `screenshot`
funcionam. Para `evaluate`, use o `npm run dev` (3101).

## Acessibilidade e movimento

Foco visível, alvos de toque de 44px, rótulos e `aria-live` nas mudanças de status, contraste AA
em claro e escuro (a cor da escola é recalculada em `src/lib/color.ts`) e `prefers-reduced-motion`
respeitado. O único momento orquestrado é o carimbo "Pago", quando a camada de dados devolve `PAID`.
