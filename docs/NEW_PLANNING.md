# APM Digital

## 1. Visão geral

O **APM Digital** será uma plataforma destinada a escolas e redes de ensino para centralizar, organizar e automatizar a gestão financeira da APM (Associação de Pais e Mestres).

O objetivo principal é substituir o processo manual atualmente realizado através de:

* Planilhas;
* Comprovantes físicos;
* Notas fiscais impressas;
* Conversas por WhatsApp;
* Conferência manual de Pix;
* Cálculos e somas realizados no final do mês;
* Organização manual dos extratos.

A plataforma deverá centralizar todas as movimentações financeiras relacionadas à APM, permitindo identificar claramente:

* Quem realizou o pagamento;
* Qual foi a origem do dinheiro;
* Qual foi a finalidade;
* Se é uma entrada ou uma despesa;
* Se existe necessidade de reembolso;
* Se o valor já foi reembolsado;
* Qual escola está relacionada à movimentação;
* Qual período financeiro a movimentação pertence.

---

# 2. Conceito principal

A plataforma terá dois grandes fluxos financeiros:

## A. Entradas / Contribuições

São valores recebidos pela APM.

Exemplo:

Um pai acessa o link da escola pelo Instagram:

> "Contribua com a APM da Escola Merino"

Ele poderá escolher:

* R$ 20
* R$ 30
* R$ 40
* Outro valor

Após escolher o valor, o sistema deverá gerar um **Pix** para pagamento.

Quando o pagamento for identificado, a movimentação deverá ser registrada automaticamente no sistema.

Essa movimentação será classificada como:

> **CONTRIBUIÇÃO / ENTRADA**

Importante:

Essa movimentação **não deve entrar como despesa ou reembolso**, pois é dinheiro recebido pela APM.

---

# 3. Despesas e reembolsos

O segundo fluxo será utilizado principalmente por:

* Professores;
* Diretores;
* Funcionários autorizados;
* Responsáveis pela escola.

Exemplo:

Uma professora precisa comprar um material para a escola.

Ela utiliza seu próprio cartão ou dinheiro para realizar a compra.

Depois acessa o APM Digital e registra:

* Valor gasto;
* Data;
* Categoria;
* Descrição;
* Motivo da compra;
* Escola;
* Forma de pagamento;
* Nota fiscal/comprovante;
* Comprovante do pagamento.

Exemplo:

> Material escolar
> R$ 87,50
> Escola Merino
> Material para atividade pedagógica
> Pix
> Nota fiscal anexada

Esse lançamento deverá ser classificado como:

> **DESPESA / REEMBOLSO**

A gestão poderá analisar o lançamento e:

* Aprovar;
* Recusar;
* Solicitar correção;
* Marcar como reembolsado.

---

# 4. Separação entre entrada e despesa

Essa é uma regra fundamental da plataforma.

O sistema **não pode misturar contribuições recebidas com despesas realizadas**.

Deverão existir categorias financeiras distintas:

### ENTRADAS

* Contribuição de pais;
* Doações;
* Outras entradas;
* Receitas da APM.

### SAÍDAS

* Reembolso de professor;
* Reembolso de diretor;
* Compra de material;
* Serviços;
* Outras despesas autorizadas.

Isso permitirá que a gestão visualize claramente:

> Quanto entrou
> Quanto foi gasto
> Quanto foi reembolsado
> Quanto ainda precisa ser reembolsado
> Quanto foi devolvido
> Saldo do período

---

# 5. Identificação da origem do pagamento

Toda movimentação deverá possuir uma **origem**.

Exemplos:

* Pai / responsável;
* Professor;
* Diretor;
* Funcionário;
* Gestão;
* APM;
* Outro.

Além disso, deverá existir a finalidade da movimentação.

Exemplo:

```text
Origem: Pai
Tipo: Entrada
Finalidade: Contribuição APM
Valor: R$ 30,00
Escola: Escola Merino
Data: 01/10/2026
```

Outro exemplo:

```text
Origem: Professora Maria
Tipo: Despesa
Finalidade: Compra de material escolar
Valor: R$ 87,50
Escola: Escola Merino
Status: Aguardando reembolso
```

---

# 6. Portal público de contribuição

Cada escola poderá possuir um link público próprio.

Exemplo:

```text
apmdigital.com.br/escola/merino
```

Esse link poderá ser divulgado:

* Instagram;
* Site da escola;
* WhatsApp;
* Comunicados;
* QR Code;
* Materiais impressos.

Ao acessar, o responsável poderá visualizar:

> Contribua com a APM da Escola Merino

E selecionar:

```text
R$ 20
R$ 30
R$ 40
Outro valor
```

Depois:

1. Seleciona o valor;
2. Confirma a contribuição;
3. O sistema gera o Pix;
4. O pagamento é processado;
5. O sistema identifica o pagamento;
6. A contribuição aparece automaticamente na gestão.

---

# 7. Identificação da contribuição

A contribuição deverá armazenar informações suficientes para que a gestão consiga identificar sua origem.

Possíveis informações:

* Nome do contribuinte;
* E-mail;
* Telefone, opcional;
* Escola;
* Valor;
* Data;
* ID da transação;
* Status do pagamento;
* Tipo da contribuição.

Exemplo:

```text
João da Silva
Escola Merino
Contribuição APM
R$ 30,00
Pago
01/10/2026
```

O sistema deve evitar exigir informações desnecessárias do responsável.

---

# 8. Fluxo de reembolso

O fluxo de reembolso deverá funcionar da seguinte maneira:

### 1. Funcionário realiza a compra

Exemplo:

> Professora compra material de R$ 120.

### 2. Funcionário registra a despesa

Informa:

* Valor;
* Data;
* Categoria;
* Descrição;
* Escola;
* Forma de pagamento;
* Comprovante;
* Nota fiscal.

### 3. Solicitação entra na gestão

Status:

> Aguardando análise

### 4. Gestão analisa

Pode:

* Aprovar;
* Recusar;
* Solicitar correção.

### 5. Reembolso

Após aprovação, a gestão realiza o reembolso.

O sistema deverá registrar:

```text
Valor solicitado: R$ 120,00
Valor aprovado: R$ 120,00
Valor reembolsado: R$ 120,00
Data do reembolso: 05/10/2026
Status: Reembolsado
```

---

# 9. Devoluções

Também deverá existir o conceito de **devolução**.

Exemplo:

Um pagamento foi realizado incorretamente ou sobrou dinheiro de uma compra.

A pessoa devolve R$ 20 para a APM.

Esse valor deverá ser registrado separadamente.

Exemplo:

```text
Tipo: Devolução
Origem: Professora Maria
Valor: R$ 20,00
Motivo: Saldo não utilizado
Data: 10/10/2026
```

A devolução deve aparecer no fechamento financeiro, pois altera o valor líquido da movimentação.

---

# 10. Dashboard da gestão

A gestão deverá possuir um dashboard financeiro.

### Indicadores principais

```text
Entradas
R$ 4.850,00

Despesas
R$ 2.350,00

Reembolsos pendentes
R$ 450,00

Reembolsos realizados
R$ 1.900,00

Devoluções
R$ 120,00

Saldo
R$ 2.500,00
```

Os valores devem poder ser filtrados por:

* Escola;
* Período;
* Mês;
* Tipo;
* Categoria;
* Pessoa;
* Status.

---

# 11. Extrato financeiro

A plataforma deverá possuir um extrato semelhante a uma movimentação bancária.

Exemplo:

| Data  | Tipo      | Origem | Descrição           | Entrada |    Saída | Status      |
| ----- | --------- | ------ | ------------------- | ------: | -------: | ----------- |
| 01/10 | Entrada   | João   | Contribuição APM    |   R$ 30 |        - | Pago        |
| 02/10 | Despesa   | Maria  | Material escolar    |       - | R$ 87,50 | Reembolsado |
| 03/10 | Entrada   | Ana    | Contribuição APM    |   R$ 40 |        - | Pago        |
| 05/10 | Devolução | Maria  | Saldo não utilizado |   R$ 20 |        - | Confirmado  |

Esse extrato deverá ser gerado automaticamente a partir das movimentações registradas na plataforma.

---

# 12. Fechamento mensal

Uma das funcionalidades mais importantes será o **fechamento mensal da APM**.

No final de cada mês, a plataforma deverá consolidar automaticamente todas as movimentações.

Exemplo:

## Outubro/2026

### Entradas

Contribuições:

> R$ 4.850,00

Outras entradas:

> R$ 500,00

Total de entradas:

> R$ 5.350,00

### Saídas

Despesas/reembolsos:

> R$ 2.350,00

### Devoluções

> R$ 120,00

### Resultado do período

O sistema deverá calcular automaticamente o saldo do período com base nas movimentações financeiras registradas.

---

# 13. PDF mensal

A plataforma deverá permitir gerar um **relatório financeiro mensal em PDF**.

O documento deverá conter:

## Identificação

* Escola;
* APM;
* Período;
* Data de geração.

## Resumo financeiro

* Total de entradas;
* Total de despesas;
* Total de reembolsos;
* Total de devoluções;
* Saldo do período.

## Detalhamento

Todas as movimentações do mês:

* Data;
* Tipo;
* Pessoa;
* Categoria;
* Descrição;
* Valor;
* Status.

## Comprovantes

Quando necessário, o sistema deverá permitir relacionar os comprovantes às respectivas movimentações.

---

# 14. Multi-escola / Rede de ensino

O sistema deverá ser desenvolvido pensando em **redes de ensino**.

Uma organização poderá possuir várias escolas.

Exemplo:

```text
Rede Merino
│
├── Escola Merino Centro
├── Escola Merino Norte
├── Escola Merino Sul
└── Escola Merino Leste
```

Cada escola deverá possuir suas próprias movimentações financeiras.

A gestão da rede poderá possuir uma visão consolidada.

Exemplo:

```text
Rede Merino

Escola Centro
Entradas: R$ 5.000
Saídas: R$ 2.000

Escola Norte
Entradas: R$ 3.500
Saídas: R$ 1.800

Escola Sul
Entradas: R$ 4.200
Saídas: R$ 2.100
```

Ao mesmo tempo, uma gestão de escola deverá visualizar apenas os dados aos quais possui permissão.

---

# 15. Controle de permissões

O sistema deverá possuir diferentes níveis de acesso.

### Administrador da rede

Pode:

* Criar escolas;
* Gerenciar usuários;
* Visualizar todas as escolas;
* Visualizar relatórios consolidados;
* Configurar a plataforma.

### Gestão da escola

Pode:

* Visualizar movimentações da escola;
* Aprovar despesas;
* Gerenciar reembolsos;
* Visualizar contribuições;
* Gerar extratos;
* Gerar PDFs;
* Acompanhar saldo.

### Professor / Funcionário

Pode:

* Registrar despesas;
* Solicitar reembolso;
* Anexar notas fiscais;
* Anexar comprovantes;
* Acompanhar seus próprios reembolsos.

### Responsável / Pai

Não precisa possuir uma conta completa na plataforma.

Deverá utilizar o portal público para:

* Escolher valor;
* Realizar contribuição;
* Efetuar pagamento;
* Receber confirmação.

---

# 16. Status das movimentações

Todas as movimentações deverão possuir estados claros.

### Despesas

```text
Rascunho
Aguardando análise
Correção solicitada
Aprovada
Recusada
Aguardando reembolso
Reembolsada
```

### Contribuições

```text
Pendente
Pago
Cancelado
Expirado
```

### Devoluções

```text
Solicitada
Aguardando confirmação
Confirmada
Recusada
```

---

# 17. Auditoria

Por se tratar de informações financeiras, o sistema deverá possuir histórico das ações.

Registrar:

* Quem criou;
* Quem alterou;
* Quem aprovou;
* Quem recusou;
* Quem realizou o reembolso;
* Data e horário;
* Alterações realizadas.

Exemplo:

```text
05/10/2026 14:32
Maria Silva aprovou a despesa #1023.

05/10/2026 15:10
João Souza registrou o reembolso da despesa #1023.
```

Isso permitirá rastrear todo o histórico financeiro.

---

# 18. Objetivo final

O APM Digital deve eliminar a necessidade de a escola ficar:

* Montando planilhas manualmente;
* Somando comprovantes;
* Contando linhas;
* Procurando notas fiscais;
* Conferindo conversas;
* Separando manualmente contribuições e despesas;
* Montando o fechamento mensal.

A plataforma deverá transformar todas essas informações em um fluxo único:

```text
CONTRIBUIÇÃO
      ↓
PIX
      ↓
PAGAMENTO
      ↓
REGISTRO AUTOMÁTICO
      ↓
EXTRATO


DESPESA
      ↓
PROFESSOR / FUNCIONÁRIO
      ↓
COMPROVANTE + NOTA
      ↓
ANÁLISE DA GESTÃO
      ↓
APROVAÇÃO
      ↓
REEMBOLSO
      ↓
EXTRATO


TODAS AS MOVIMENTAÇÕES
      ↓
CONSOLIDAÇÃO MENSAL
      ↓
FECHAMENTO
      ↓
RELATÓRIO PDF
```

O princípio central do produto é:

> **Cada movimentação financeira precisa possuir uma origem, uma finalidade, um responsável, um status e uma escola associada.**

Dessa forma, a APM deixa de depender de controles manuais e passa a possuir uma visão financeira organizada, rastreável e centralizada.

# 19. Planejamento técnico — Pix e segurança

A plataforma deverá possuir integração com um provedor de pagamentos/banco que disponibilize **API Pix**.

O APM Digital **não deve armazenar nem manipular diretamente credenciais bancárias no frontend**. O backend será responsável pela comunicação com o provedor Pix.

## Arquitetura

```text
Usuário
   ↓
Next.js
   ↓
Python API
   ↓
Pix Service
   ↓
Provedor / Banco
   ↓
Conta da APM
```

O frontend nunca deverá possuir:

* Client Secret;
* Tokens privados;
* Certificados;
* Chaves privadas;
* Credenciais bancárias.

Essas informações deverão permanecer exclusivamente no backend e/ou em um sistema seguro de gerenciamento de secrets.

---

## 19.1 Cobrança Pix dinâmica

Não utilizar apenas uma chave Pix fixa para as contribuições.

Cada contribuição deverá gerar uma **cobrança Pix dinâmica**, permitindo identificar individualmente cada pagamento.

Exemplo:

```text
Cobrança: #APM-20261001-000123
Escola: Escola Merino
Tipo: Contribuição
Valor: R$ 30,00
Status: Pendente
```

A API do provedor deverá retornar os dados necessários para gerar:

* QR Code;
* Pix Copia e Cola;
* Identificador da cobrança.

O usuário realizará o pagamento utilizando esses dados.

---

## 19.2 Confirmação do pagamento

O frontend não deverá determinar se um Pix foi pago.

O status do pagamento deverá ser confirmado através da integração com o provedor.

Fluxo:

```text
Usuário gera cobrança
        ↓
QR Code / Pix Copia e Cola
        ↓
Usuário realiza pagamento
        ↓
Banco processa pagamento
        ↓
Provedor envia webhook
        ↓
Python API recebe evento
        ↓
Backend valida evento
        ↓
Backend confirma cobrança
        ↓
Movimentação = PAGA
```

---

## 19.3 Webhook

O sistema deverá possuir um endpoint específico para receber notificações do provedor Pix.

Exemplo conceitual:

```text
POST /webhooks/pix
```

O webhook deverá ser tratado como uma entrada externa e deverá passar por validações antes de alterar qualquer informação financeira.

Sempre que possível, utilizar os mecanismos de segurança disponibilizados pelo provedor, como:

* Assinatura;
* Certificado;
* Token;
* Autenticação;
* Validação da origem;
* Outros mecanismos oficiais disponibilizados pelo provedor.

Após receber o webhook, o backend deverá validar a cobrança.

Quando necessário, o backend deverá consultar novamente a API do provedor para confirmar:

```text
ID da cobrança
Valor esperado
Valor recebido
Status
Data do pagamento
Conta recebedora
```

Somente após a validação a movimentação deverá ser marcada como paga.

---

## 19.4 Validação do valor

O sistema deverá comparar o valor esperado com o valor efetivamente recebido.

Exemplo:

```text
Valor esperado: R$ 30,00
Valor recebido: R$ 30,00
Status: Pago
```

Caso exista divergência:

```text
Valor esperado: R$ 30,00
Valor recebido: R$ 300,00
```

A movimentação não deverá ser automaticamente processada como uma contribuição normal.

Ela deverá ser marcada para análise.

---

## 19.5 Conta Pix por escola

Como o APM Digital será destinado a redes de ensino, deverá existir uma associação entre escola e conta de recebimento.

Exemplo:

```text
Rede Merino
│
├── Escola Centro
│   └── Conta APM Centro
│
├── Escola Norte
│   └── Conta APM Norte
│
└── Escola Sul
    └── Conta APM Sul
```

A URL pública da escola deverá determinar qual organização/escola está recebendo a contribuição.

Exemplo:

```text
apmdigital.com.br/escola/merino
```

O backend deverá identificar a escola e utilizar a configuração de pagamento correspondente.

---

## 19.6 Configuração da conta de pagamento

A aplicação deverá possuir uma entidade semelhante a:

```text
PaymentAccount
```

Exemplo conceitual:

```text
PaymentAccount
-------------------------
id
school_id
provider
external_account_id
status
created_at
updated_at
```

Não armazenar desnecessariamente dados bancários sensíveis diretamente nessa tabela.

Credenciais, certificados, chaves privadas e secrets deverão permanecer em um mecanismo seguro de gerenciamento de credenciais.

---

## 19.7 Segurança das credenciais

As credenciais da integração Pix deverão:

* Permanecer somente no backend;
* Nunca ser expostas ao frontend;
* Nunca utilizar `NEXT_PUBLIC_*`;
* Nunca ser commitadas no Git;
* Nunca aparecer em logs;
* Ser armazenadas como secrets no ambiente de produção;
* Possuir rotação quando suportada pelo provedor.

Exemplo conceitual:

```text
PIX_CLIENT_ID
PIX_CLIENT_SECRET
PIX_CERTIFICATE
PIX_PRIVATE_KEY
```

Esses valores deverão ser acessados exclusivamente pelo serviço responsável pela integração Pix.

---

## 19.8 Idempotência

O processamento de pagamentos deverá ser idempotente.

O mesmo webhook pode ser recebido mais de uma vez.

Exemplo:

```text
Webhook 1 → cobrança 123 → pago
Webhook 2 → cobrança 123 → pago
Webhook 3 → cobrança 123 → pago
```

O sistema não pode registrar três contribuições.

Deverá existir uma identificação única da cobrança/transação para garantir que o mesmo pagamento seja processado apenas uma vez.

---

## 19.9 Auditoria financeira

Toda alteração relacionada a pagamentos deverá possuir registro de auditoria.

Registrar, quando aplicável:

* Criação da cobrança;
* Atualização de status;
* Recebimento do webhook;
* Confirmação do pagamento;
* Cancelamento;
* Estorno/devolução;
* Usuário responsável por alterações manuais;
* Data e horário;
* Identificador externo da transação.

---

# 20. Separação dos fluxos financeiros

A plataforma deverá tratar os fluxos financeiros de maneira independente.

```text
                    APM DIGITAL
                         │
              ┌──────────┴──────────┐
              │                     │
           ENTRADAS               SAÍDAS
              │                     │
       Contribuições           Despesas
              │                     │
             Pix                Reembolso
              │                     │
              ↓                     ↓
        Conta da APM          Aprovação da gestão
              │                     │
              └──────────┬──────────┘
                         ↓
                    EXTRATO
                         ↓
                FECHAMENTO MENSAL
                         ↓
                     PDF
```

### Entrada

Exemplo:

```text
Tipo: CONTRIBUIÇÃO
Origem: Pai / Responsável
Valor: R$ 30,00
Status: Pago
```

### Saída

Exemplo:

```text
Tipo: DESPESA
Origem: Professora Maria
Valor: R$ 87,50
Status: Reembolsado
```

Esses dois fluxos nunca deverão ser tratados como a mesma operação financeira.

---

# 21. MVP da integração Pix

A primeira versão deverá priorizar simplicidade e segurança.

### MVP

* Uma integração com provedor Pix;
* Uma conta de recebimento por escola;
* Criação de cobrança dinâmica;
* QR Code;
* Pix Copia e Cola;
* Webhook;
* Validação do pagamento;
* Controle de status;
* Idempotência;
* Registro da transação;
* Auditoria;
* Associação entre cobrança e escola;
* Registro automático da contribuição no extrato.

### Futuramente

A arquitetura deverá permitir evolução para:

* Múltiplos provedores;
* Múltiplas contas por organização;
* Estornos;
* Devoluções;
* Conciliação bancária;
* Relatórios financeiros avançados;
* Integrações bancárias adicionais.

---

# 22. Regra principal de segurança

O sistema deverá seguir o seguinte princípio:

> **O APM Digital não deve confiar no frontend para determinar o estado financeiro de uma operação.**

O frontend apenas inicia a operação e apresenta informações ao usuário.

A confirmação financeira deverá sempre partir do backend, utilizando informações fornecidas e validadas através do provedor de pagamento.

```text
Frontend
   ↓
Solicita cobrança
   ↓
Backend
   ↓
Provedor Pix
   ↓
Banco
   ↓
Webhook
   ↓
Backend valida
   ↓
Banco de dados
   ↓
Extrato atualizado
```

Essa arquitetura deverá ser considerada parte fundamental do projeto desde o início, mesmo no MVP, pois a plataforma irá lidar diretamente com informações e movimentações financeiras.
