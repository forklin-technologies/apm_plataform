# LEGADO — somente referência

Este diretório contém o código anterior do projeto (Next.js full-stack em
TypeScript + Drizzle, tenant apenas por `escola_id`). Ele NÃO é a arquitetura
final (ADR-007).

- Não adicione funcionalidades aqui.
- Não importe código daqui para `apps/`. Use como referência de regras e
  cenários (Pix, RLS, estoque, testes) e reescreva na stack nova.
- Stack nova: `apps/web` (Next.js) + `apps/api` (Python/FastAPI) + PostgreSQL.
