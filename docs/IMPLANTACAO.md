# Implantação

## Componentes

| Serviço | Comando | Observação |
|---|---|---|
| Aplicação web | `npm run build && npm start` | Atrás do proxy |
| Rotinas | `npm run jobs` | Um único processo |
| PostgreSQL 16 | gerenciado | Backup contínuo (PITR) + dump diário fora do provedor |
| Proxy (Caddy) | `deploy/Caddyfile` | TLS público + mTLS no domínio do webhook |

## Banco de dados

```bash
# 1) Papel da aplicação, SEM superusuário (o RLS não vale para superusuário)
psql -c "CREATE ROLE apm_app LOGIN PASSWORD '...';"
# 2) Migrações + políticas de RLS (executar com o dono das tabelas)
DATABASE_ADMIN_URL=postgres://dono:...@host/apm npm run db:migrate
# 3) Escola piloto
DATABASE_URL=postgres://apm_app:...@host/apm APP_ENCRYPTION_KEY=... npm run db:seed
```

## Webhook e mTLS

O padrão Pix do Banco Central prevê que o banco se autentique com certificado
ao chamar o webhook. Por isso o webhook fica num subdomínio próprio
(`webhook.seudominio`) em que o proxy **exige** certificado de cliente emitido
pela cadeia informada pelo banco. Plataformas serverless não oferecem isso —
use VPS ou PaaS com contêiner.

O proxy também deve **sobrescrever** `X-Real-IP` (usado no limite de
requisições), nunca repassar o valor enviado pelo cliente.

## Variáveis de ambiente

Ver `.env.example`. Além delas, é possível sobrescrever as URLs do BB
(`BB_PIX_URL_PRODUCAO`, `BB_OAUTH_URL_PRODUCAO`, `..._HOMOLOGACAO`), o nome do
parâmetro da chave de aplicação (`BB_APP_KEY_PARAM`) e os escopos (`BB_ESCOPOS`)
caso o Portal Developers BB indique valores diferentes dos padrões do código.

## Segurança operacional

- `NODE_ENV=production` desativa o provedor sandbox e a rota `/api/sandbox/*`.
- Credencial com provedor `sandbox` em produção faz a página responder
  "Pix não configurado" — nunca gera cobrança falsa.
- Restauração de backup testada mensalmente.
