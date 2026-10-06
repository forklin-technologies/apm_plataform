# Propostas de contrato da API para o frontend (apps/web)

> **Status: PROPOSTA.** Nada abaixo existe na API hoje. É o que o frontend vai precisar para
> trocar os mocks (`apps/web/src/mocks`) por chamadas reais, para o Backend e o Architect
> revisarem. O frontend não decide nada disto: se o Backend preferir outro desenho, só
> `apps/web/src/lib/api/` muda.
>
> **Atualização (TASK-011, fatia 2):** o portal público (`/escola/{slug}`) e o painel (resumo, extrato,
> pendências, contribuição em dinheiro, PDF) agora são **reais**: valem `docs/public-flow.md` e
> `docs/statement.md`, e esta página é só histórico. Nada do site é simulado.
>
> **Atualização (TASK-011, fatia 1):** a **autenticação já é REAL** e vale `docs/auth.md` e o
> ADR-017 (prefixo `/api/v1`, erros em `application/problem+json` com `code`, cookies `apm_session`
> HttpOnly e `apm_csrf` legível, `X-CSRF-Token` nos pedidos que mudam dados). Onde esta página
> divergir (formato de erro `{code, message}`, `/api/auth/*`, `GET /api/me/organizations`), vale o
> `auth.md`/ADR-017. O resto (portal público, Pix, comprovante, indicadores, movimentações) continua
> proposta e simulado.

## O que é REAL hoje

| Endpoint | Resposta | Uso no web |
|---|---|---|
| `GET /api/health` | `200 {"status":"ok"}` | não usado |
| `GET /api/health/ready` | `200 {"status":"ready"}` ou `503 {"status":"unavailable"}` | chip de status da API |
| `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`, `POST /api/v1/auth/context`, `POST /api/v1/auth/password`, `POST /api/v1/invitations/accept` | ver `docs/auth.md` | `/login`, sessão e seletor de vínculos do `/painel`, `/accept-invitation` (`src/lib/api/auth.ts`) |

Fonte: `/api/openapi.json` da API em `http://127.0.0.1:8001`.

## Convenções que o frontend assume (a confirmar)

- **Mesma origem.** O navegador só chama `/api/*`. Em dev e na prévia, o Next faz rewrite para
  `API_PROXY_URL`. Em produção, o proxy reverso (ADR-009). Sem CORS.
- **Dinheiro**: sempre inteiro em centavos, campos com sufixo `_cents` (ex.: `amount_cents`).
  Nunca decimal nem string.
- **Datas**: ISO 8601 com fuso (`2026-09-30T16:42:00-03:00`). O web formata em America/Sao_Paulo.
- **Nomes de campo**: o backend (FastAPI) deve falar `snake_case`. O web usa `camelCase` nos tipos
  de `src/lib/api/types.ts` e converte na fronteira (`src/lib/api/`).
- **Erros**: corpo `{"code": "<MACHINE_CODE>", "message": "<texto>"}`. O web exibe os próprios
  textos em pt-BR a partir do `code`; `message` não é mostrada ao usuário.
- **Enums de status em inglês**, em maiúsculas (abaixo). O web mapeia para rótulos em português.
- **Tenant nunca vem do cliente** (ADR-004, ADR-010): no portal público é o `slug` da URL; no
  painel é a sessão. O seletor de organização/escola do painel é só visual.
- **Respostas de erro iguais** para "não existe" e "não é seu" nos endpoints públicos (sem
  enumeração de escolas ou pedidos).
- **O frontend nunca afirma pagamento sem a camada de dados** (ADR-005, ADR-011): o HTML de um
  comprovante renderizado no servidor só pode dizer "Pago" se o próprio servidor recebeu `PAID` do
  backend; senão o estado é neutro ("Conferindo seu comprovante") até a resposta chegar.
- **O navegador não retém dado pessoal.** Responsável, aluno e turma vivem só no servidor. (O mock do
  protótipo os guarda em `sessionStorage` por até 30 min e os apaga depois de mostrar o comprovante;
  o backend real não precisa disso.)

## Portal público (sem login, ADR-010)

### P1. `GET /api/public/schools/{slug}`

Configuração pública da escola. `404` idêntico para slug inexistente.

```json
{
  "slug": "escola-exemplo",
  "name": "Escola Exemplo",
  "apm_name": "APM da Escola Exemplo",
  "accent_color": "#E6457A",
  "quotas": [
    { "id": "cota-anual", "name": "Cota anual", "description": "Um único pagamento para o ano letivo", "amount_cents": 20000 }
  ],
  "custom_amount": { "min_cents": 500, "max_cents": 200000 },
  "identification": {
    "guardian_name": "REQUIRED",
    "student_name": "REQUIRED",
    "classroom": "OPTIONAL"
  }
}
```

- `identification.*` ∈ `REQUIRED | OPTIONAL | HIDDEN` (campos simples, configuráveis por escola;
  não é matrícula).
- `accent_color`: hex `#rgb` ou `#rrggbb`. O web calcula o contraste AA a partir dela e cai numa
  cor padrão se vier inválida; o backend deve validar o formato ao gravar.

### P2. `POST /api/public/schools/{slug}/contributions`

Cria a contribuição e a cobrança Pix. Cabeçalho `Idempotency-Key` (UUID gerado no cliente por
tentativa). Rate limit por IP. **O backend revalida tudo**: valor mínimo/máximo da escola, campos
obrigatórios, tamanhos.

```json
{
  "amount": { "kind": "QUOTA", "quota_id": "cota-anual" },
  "identification": { "guardian_name": "Ana Lima", "student_name": "Davi Lima", "classroom": "4º B" }
}
```

ou `"amount": { "kind": "CUSTOM", "amount_cents": 1550 }`.

`201`:

```json
{
  "token": "<opaco, base64url, >= 128 bits de entropia (22 caracteres), gerado no servidor>",
  "charge": {
    "status": "PENDING",
    "amount_cents": 20000,
    "payload": "<texto do Pix copia e cola>",
    "created_at": "...",
    "expires_at": "...",
    "paid_at": null
  }
}
```

`422` com `code` (`AMOUNT_BELOW_MIN`, `AMOUNT_ABOVE_MAX`, `FIELD_REQUIRED`, ...) e, quando couber,
`field`. O web já trata `422` como "dado não aceito pela escola".

### P3. `GET /api/public/schools/{slug}/contributions/{token}/charge`

Consulta do status (polling a cada ~2 s enquanto `PENDING`). **Só o backend muda o status**, a
partir do webhook validado e idempotente do banco, mais a reconsulta à API do banco
(ADR-005, ADR-011). O frontend nunca envia "paguei".

`status` ∈ `PENDING | PAID | EXPIRED | CANCELLED`. Resposta no mesmo formato de `charge` do P2.
`404` para token desconhecido ou de outra escola.

### P4. `GET /api/public/schools/{slug}/contributions/{token}/receipt`

Comprovante, acessado por link com token (sem login). `200` só quando `PAID`; `409` caso contrário;
`404` **idêntico** para token que nunca existiu, token de outra escola e token malformado (sem
enumeração). O web só aceita tokens de 22 a 64 caracteres base64url; formato fora disso nem chega à API.

```json
{
  "number": "2026-004817",
  "school_name": "Escola Exemplo",
  "apm_name": "APM da Escola Exemplo",
  "description": "Cota anual",
  "amount_cents": 20000,
  "status": "PAID",
  "paid_at": "2026-09-30T16:42:00-03:00",
  "identification": { "guardian_name": "Ana Lima", "student_name": "Davi Lima", "classroom": "4º B" }
}
```

Cabeçalhos: `Cache-Control: private, no-store`, `Referrer-Policy: no-referrer`.

### P5. `POST /api/public/schools/{slug}/contributions/{token}/charges` (opcional)

Gera um novo Pix para a mesma contribuição quando o anterior expirou. Hoje o web cria uma nova
contribuição (P2) com os mesmos dados; este endpoint evita pedidos duplicados.

## Autenticação (ADR-009, ADR-016, ADR-017)

**Já é real** (TASK-004 no backend, TASK-011 no web): ver `docs/auth.md`. Esta seção era a proposta
original (`/api/auth/*`) e foi substituída pelos endpoints `/api/v1/auth/*` e
`/api/v1/invitations/accept`.

## Painel (precisa de sessão)

### D1. `GET /api/me/organizations`

Organizações e escolas **que a sessão pode acessar** (ADR-004). Alimenta o seletor visual.

```json
[{ "id": "...", "name": "Rede Aurora de Ensino", "schools": [{ "id": "...", "slug": "escola-exemplo", "name": "Escola Exemplo" }] }]
```

### D2. `GET /api/schools/{school_id}/dashboard?period=2026-09`

Totais **calculados pelo backend** (o web não soma nada):

```json
{
  "period_label": "Setembro de 2026",
  "balance_cents": 1087220,
  "collected_cents": 1864000, "collected_count": 112,
  "expenses_cents": 721540, "expenses_count": 9,
  "reimbursements_paid_cents": 31240,
  "reimbursements_pending_cents": 8650, "reimbursements_pending_count": 1,
  "refunds_cents": 24000, "refunds_count": 2,
  "weekly": [{ "label": "1 a 6", "in_cents": 384000, "out_cents": 125000 }]
}
```

`balance_cents` = entradas menos despesas, reembolsos pagos e devoluções (regra do backend; o
web só mostra a frase). `school_id` na URL é conferido contra a sessão no servidor.

### D3. `GET /api/schools/{school_id}/movements?kind=&status=&cursor=&limit=`

Os **quatro tipos distintos** do domínio (ADR-006): `CONTRIBUTION | EXPENSE | REIMBURSEMENT | REFUND`.

```json
{
  "items": [{
    "id": "...", "kind": "CONTRIBUTION", "status": "CONFIRMED", "direction": "IN",
    "title": "Cota anual", "counterparty": "Mariana Souza, 4º B",
    "amount_cents": 20000, "occurred_at": "2026-09-30T16:42:00-03:00"
  }],
  "next_cursor": null
}
```

- `direction` ∈ `IN | OUT` **vem do backend**: a tela não deduz entrada/saída pelo tipo.
- `status` ∈ `PENDING | APPROVED | CONFIRMED | REJECTED | CANCELLED | EXPIRED`. Combinações
  válidas por tipo (o web mostra "indisponível" para qualquer outra):
  - `CONTRIBUTION`: PENDING, CONFIRMED, EXPIRED, CANCELLED
  - `EXPENSE`: PENDING, CONFIRMED, CANCELLED
  - `REIMBURSEMENT`: PENDING, APPROVED, CONFIRMED, REJECTED
  - `REFUND`: PENDING, CONFIRMED, REJECTED
- `counterparty` pode conter dado pessoal: precisa respeitar o escopo de quem consulta.

## Perguntas em aberto para o Backend / Architect

1. **QR Code**: o backend devolve só o `payload` (o web desenha o QR com uma biblioteca) ou também
   uma imagem/SVG? Hoje o web mostra um QR **de exemplo, impossível de ler** de propósito.
2. **Polling × SSE** para o status do Pix. Polling de 2 s está no web; SSE reduziria carga.
3. **TTL da cobrança** (o mock usa 10 min) e política de nova cobrança (P5).
4. **Cancelamento**: a família precisa poder cancelar um Pix pendente? Hoje o web só deixa expirar.
5. **Paginação e filtros** de D3 (período, busca por nome) e exportação de extratos/relatórios.
6. **Fuso**: períodos mensais em America/Sao_Paulo?
7. **CSP**: o web usa nonce por requisição (`apps/web/src/proxy.ts`). O proxy reverso de produção
   não deve sobrescrever o cabeçalho `Content-Security-Policy` das páginas.
