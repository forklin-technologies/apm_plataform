# Plataforma APM

Contribuições, campanhas e vendas da APM escolar com Pix direto na conta da
APM. Escola piloto: EMEB Aparecida Merino Elias.

Documentação: [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) ·
[`docs/BANCO_DO_BRASIL.md`](docs/BANCO_DO_BRASIL.md) ·
[`docs/IMPLANTACAO.md`](docs/IMPLANTACAO.md)

## Estado do MVP

| Etapa | Situação |
|---|---|
| 1.1 Base, banco, migrações, seed da escola piloto | ✅ (autenticação do painel na próxima entrega) |
| 1.2 Multi-tenant com RLS | ✅ isolamento testado · super admin: próxima entrega |
| 1.3 Campanhas e itens | ✅ modelo e regras · telas do painel: próxima entrega |
| 1.4 Página pública, carrinho, identificação | API pronta · telas: próxima entrega |
| 1.5 Pedidos, estoque, camada Pix, Banco do Brasil, sandbox | ✅ |
| 1.6 Webhook, reconsulta, expiração, conciliação | ✅ |
| 1.7 Tela do Pix e comprovante | API e QR Code prontos · telas: próxima entrega |
| 1.8 Dashboard, lista de pedidos, lançamento em dinheiro | a fazer |
| 1.9 Testes e guia de implantação | ✅ 25 testes · guia em `docs/` |

## Rodando localmente

```bash
cp .env.example .env        # preencha APP_ENCRYPTION_KEY
npm install
npm run db:migrate          # usa DATABASE_ADMIN_URL
npm run db:seed
npm run dev                 # http://localhost:3000
npm run jobs                # em outro terminal
npm test                    # precisa de um banco apm_test
```

## API (usada pelas telas)

| Método | Rota | Função |
|---|---|---|
| POST | `/api/escolas/{slug}/pedidos` | Cria pedido + Pix. O corpo informa só itens, quantidades, meses e identificação; o preço é calculado no servidor. |
| GET | `/api/escolas/{slug}/pedidos/{token}` | Situação do pedido (a tela consulta a cada poucos segundos) |
| GET | `/api/escolas/{slug}/pedidos/{token}/qrcode` | QR Code SVG |
| POST | `/api/webhooks/pix/{escolaId}/{segredo}` | Webhook do banco (mTLS no proxy) |
| POST | `/api/sandbox/pagar` | **Só desenvolvimento**: simula o pagamento |

## Organização

```
src/db/          esquema, conexão com contexto de escola (RLS), dados iniciais
src/pix/         interface PixProvider, padrão Bacen, Banco do Brasil, sandbox
src/services/    pedidos, estoque, confirmação, rotinas, consultas
src/app/api/     rotas HTTP
drizzle/         migrações SQL + políticas de RLS
scripts/         migração, seed, processo de rotinas
tests/           unidade, adaptador BB, integração com PostgreSQL
```
