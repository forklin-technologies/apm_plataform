# Checklist da APM — ativar o Pix automático no Banco do Brasil

Este roteiro é para a diretoria/tesouraria da APM. Nenhuma etapa envolve
passar senha bancária para a plataforma: o sistema recebe apenas credenciais
de API, que permitem **gerar cobranças e consultar recebimentos** — não
permitem fazer transferências de saída.

> Os nomes de telas abaixo podem mudar no portal do BB. Em caso de dúvida,
> o gerente PJ da agência pode orientar sobre a "API Pix".

## 1. Pré-requisitos (já atendidos)

- [x] Conta PJ da APM no Banco do Brasil
- [x] Chave Pix CNPJ 03.604.104/0001-01

## 2. Perguntar ao gerente PJ

- [ ] A API Pix (v2) pode ser habilitada para a conta da APM?
- [ ] Há tarifa por Pix recebido via cobrança (QR Code dinâmico)? Qual?
- [ ] Qual certificado digital o BB aceita para a conexão mTLS da API Pix v2
      (ex.: e-CNPJ A1 da APM ou outro)? A APM já possui algum?
- [ ] Quem da APM precisa aprovar a liberação em produção (representante legal)?

## 3. Portal Developers BB (feito junto com o responsável técnico)

- [ ] Criar a aplicação no Portal Developers BB e vincular a **API Pix**
- [ ] Anotar as credenciais de **homologação**: Client ID, Client Secret e
      chave de aplicação (developer application key)
- [ ] Enviar o certificado digital (mTLS) no portal
- [ ] Testar em homologação (o sistema tem testes prontos para isso)
- [ ] Solicitar **produção** informando o CNPJ da APM
- [ ] Anotar as credenciais de **produção**

## 4. Cadastro no sistema

- [ ] Cadastrar as credenciais pela tela de configuração do Pix (fase 1.2),
      **nunca** por WhatsApp ou e-mail
- [ ] O sistema registra o webhook no BB automaticamente
- [ ] Fazer um Pix real de R$ 1,00 e conferir no extrato da APM e no painel

## Quem guarda o quê

| Item | Onde fica |
|---|---|
| Senha do internet banking | Só com a diretoria da APM. Nunca vai para o sistema. |
| Client ID / Secret / chave de aplicação | Cifrados no banco de dados do sistema (AES-256-GCM) |
| Certificado digital | Cifrado no banco de dados do sistema; cópia de segurança com a APM |
| Chave mestra de criptografia | Variável de ambiente do servidor, fora do banco |
