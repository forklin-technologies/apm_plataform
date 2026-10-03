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
- O matcher do `proxy.ts` exclui só `/api`, `/api/*` e os estáticos: `/apix`, `/api-docs` e `/apiary`
  recebem a CSP com nonce (testado em unidade e em e2e).
- Páginas de erro (`error.tsx`, `global-error.tsx`) em português, sem expor `error.message`.
- Sem `dangerouslySetInnerHTML` (regra de lint). Sem segredos e sem `NEXT_PUBLIC_*`.
- Em produção, o proxy reverso não deve sobrescrever o `Content-Security-Policy` das páginas.

## Comprovante e privacidade (dado de criança)

- **O comprovante só afirma o que a camada de dados confirma.** Só o link de exemplo
  (`/apm/escola-exemplo/pedido/demo-comprovante-0001`) tem comprovante fixo. Para qualquer outro token o
  HTML do servidor sai **neutro** ("Conferindo seu comprovante": sem "Pago", sem nome, sem valor) e quem
  responde é a camada de dados, no navegador: comprovante, "ainda não confirmado" ou a mesma 404
  estilizada (sem diferenciar "nunca existiu" de "ainda não existe").
- Token do mock: 16 bytes de `crypto.getRandomValues` em base64url (22 caracteres, 128 bits, sem viés de
  módulo). O frontend só confere o formato (22 a 64 caracteres seguros para URL); token curto é 404 de
  verdade (HTTP).
- No protótipo o mock guarda o pedido em `sessionStorage` só durante o fluxo: o nome do responsável, do
  aluno e a turma são **apagados depois que o comprovante é mostrado**, e o pedido inteiro expira em 30
  minutos mesmo que o comprovante nunca seja aberto. O link segue abrindo (sem os nomes) até o TTL.
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
