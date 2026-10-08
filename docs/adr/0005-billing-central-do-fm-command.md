# ADR-0005 — Billing Central do FM Command: o AtendeVendeIA obedece à licença

Status: **DIRETRIZ E COMPLEMENTO APROVADOS pelo Diretor em 08/10/2026; detalhamento técnico PROPOSTO e sem implementação** · Data: 2026-10-08
Auditoria que sustenta este ADR: `docs/auditoria/AUDITORIA_BILLING_CENTRAL_2026-10-08.md`. Contrato: `docs/FM_COMMAND_LICENCAS.md`.

## Contexto

O Diretor aprovou como diretriz a centralização de assinaturas e licenças no **FM Command** (repositório `FM-CONTROL-CENTER`), autoridade de billing,
assinaturas comerciais, políticas de cobrança e direitos de licença de todos os SaaS da FM Tecnologia. Os gateways (Cakto, Hotmart, Asaas, Stripe e
outros) entram por **adaptadores no Command**; nenhum SaaS depende permanentemente de integração própria para saber o estado comercial de uma assinatura.

A auditoria mostrou que **o Command ainda não tem** o Billing Central: não emite `customer_id`, `subscription_id` nem `license_id`, não recebe nem emite
webhook (o modo `webhook` existe no tipo, mas o runtime recusa) e não conhece o AtendeVendeIA. Isto é, o contrato abaixo é trabalho **novo dos dois lados**.

## Decisões da diretriz (numeração do Diretor) e como este ADR as cumpre

| # | Diretriz | Como fica |
|---|---|---|
| 1 | Command é a autoridade de billing, assinatura, cobrança e licença | O AtendeVendeIA **aplica** a licença; não decide estado comercial |
| 2 | Gateways por adaptadores no Command; nenhum SaaS depende de integração própria **permanentemente** | Cakto e Hotmart diretas ficam como **transição** (item 7); no fim, só o Command fala com gateway |
| 3 | `customer_id` imutável emitido pelo Command, mais `subscription_id` e `license_id`; e-mail não é chave primária | Tabela de vínculo no AtendeVendeIA (`commercial_links`); e-mail vira só dado de contato e do convite |
| 4 | O AtendeVendeIA cria tenant, conta, usuário administrador e convite; o Command dá a **autorização comercial** para provisionar | O evento `license.activated` traz `provisioning.authorized` e o contato do administrador; sem autorização, nada é criado |
| 5 | Webhook assinado de licença, mais **reconciliação periódica** pela API de licenças do Command | Contrato com os dois canais; a reconciliação corrige perda, atraso e divergência |
| 6 | Carência e suspensão governadas pelo Command; durante indisponibilidade, **última licença validada**, contingência limitada, auditável, sem prorrogação indefinida | `grace_ends_at` e `valid_until` vêm do Command; contingência de duração máxima (proposta: 72 h), registrada e sem renovação automática |
| 7 | Preservar Cakto e Hotmart na transição, com **um único responsável por assinatura** e sem evento duplicado | Campo `authority` por assinatura; evento de quem não é a autoridade é registrado e **não aplicado**; modo sombra antes da virada |
| 8 | Contrato com versionamento, identificação de tenant, produto, assinatura e licença, estados, vigência, carência, origem, id único do evento, assinatura criptográfica, anti-replay, idempotência, reconciliação, eventos fora de ordem e auditoria | `docs/FM_COMMAND_LICENCAS.md`, seção a seção |
| 9 | Respeitar multi-tenant, segregação de privilégios e governança da FM | Ver "Segurança e governança" abaixo |

## Decisão técnica proposta

### Papéis

- **FM Command:** emite identificadores; recebe e valida pagamentos dos gateways (por adaptadores, com consulta à API do provedor quando necessário);
  mantém assinatura e licença; decide carência e suspensão; **emite** eventos de licença e **serve** a API de licenças. Não é o gateway.
- **AtendeVendeIA:** **consome** a licença; cria e mantém tenant, conta, administrador e convite; aplica o estado (`active`, `past_due`, `suspended`,
  `canceled`, `refunded`) pelo ciclo de vida que já existe (`provisioning/lifecycle.py`); guarda o vínculo e a auditoria. Não fala com gateway no destino final.

### Vínculo e identificadores (diretriz 3)

Nova tabela do AtendeVendeIA (proposta, sem migration nesta PR): `commercial_links` com `tenant_id` (PK), `issuer` (`fmcommand`), `customer_id`,
`subscription_id`, `license_id` (todos texto opaco e imutável; únicos por `issuer`), `authority`, `license_version`, `state`, `valid_from`, `valid_until`,
`grace_ends_at`, `last_validated_at`, `contingency_until`. Com RLS forçada e leitura/escrita só em modo `system`, como `platform_events`.
`tenant_plans.source` passa a admitir `fmcommand`. O produto mantém o seu próprio `tenant_id`; o Command nunca vê nem escolhe o `tenant_id` interno.

### Responsável único por assinatura e transição (diretriz 7)

`authority` por assinatura: `direct:cakto`, `direct:hotmart` ou `fmcommand`.

1. **Hoje:** todas as assinaturas são `direct:*`; o Command não participa. O lançamento não muda.
2. **Modo sombra** (`FM_FMCOMMAND_MODE=shadow`): o AtendeVendeIA recebe e valida os eventos do Command, **registra** e compara com o estado real, mas **não aplica**.
   Divergências viram relatório. Critério para sair: período combinado sem divergência.
3. **Virada por assinatura:** o Diretor autoriza e a assinatura passa a `authority = fmcommand` (registrado na auditoria). Dali em diante, eventos
   diretos da Cakto ou da Hotmart dessa assinatura são registrados com resultado `not_authoritative` e **ignorados**. Nenhuma assinatura tem dois responsáveis.
4. **Fim:** quando todas as assinaturas estiverem no Command, os segredos da Cakto e da Hotmart são apagados (a rota volta a responder 404).

### Indisponibilidade e contingência (diretriz 6)

- O AtendeVendeIA guarda a **última licença validada** (`license_version`, vigência e carência).
- Command fora do ar **não suspende** quem estava ativo e dentro da vigência.
- Se a vigência ou a carência vencer **sem** confirmação do Command, vale a **contingência**: no máximo `contingency_hours` (proposta: 72 h, teto fixo na configuração do
  AtendeVendeIA, valor do Command nunca excede o teto), registrada na auditoria no início e no fim. **Sem prorrogação automática:** ao fim da contingência o
  estado vira `suspended` (nada é apagado). Só uma licença nova validada (evento ou reconciliação) reabre.
- A reconciliação e os eventos nunca "prorrogam sozinhos": só uma licença emitida pelo Command altera datas.

### Segurança e governança (diretriz 9)

- **Segredos separados:** um para receber eventos (`FM_PLATFORM_FMCOMMAND_SECRET`, assinatura HMAC), outro, de **leitura**, para o AtendeVendeIA consultar a API de licenças,
  com escopo `licenses:read` limitado ao produto `ATENDEVENDEIA`. O token de leitura do Command sobre o AtendeVendeIA (ADR-0004) continua separado e só leitura.
- **Sem dado em excesso:** o evento traz identificadores, estado, datas, plano e contato do administrador. Nunca cartão, CPF, endereço nem credencial de gateway.
- **Multi-tenant:** a licença é de um cliente; o handler resolve o `tenant_id` pelo vínculo e processa em modo `system` do banco, com consultas fixas, como o recebimento
  de compras de hoje. Nenhuma rota de pessoa ou de cliente toca nesse caminho.
- **Privilégio mínimo:** o Command não escreve no banco do AtendeVendeIA (regra do ADR-0004 mantida); o AtendeVendeIA não escreve no Command.
- **Auditoria:** cada evento aplicado, ignorado, divergente ou de contingência gera registro; o corpo bruto fica cifrado em `platform_events`.

## Fora do escopo do AtendeVendeIA

Gateways, conciliação financeira, taxas, impostos e lucro líquido (valor vendido, recebido e líquido) são do Command. O evento traz `payment.amount_cents` só como informação.

## Consequências

- **Nada está implementado nem testado contra o Command.** O Command precisa construir: emissão de identificadores, adaptadores de gateway, emissor de webhook assinado
  (com fila e reenvio), API de licenças e a fonte do AtendeVendeIA no Source Registry (hoje inexistente).
- O AtendeVendeIA precisa construir (futuro, com aprovação): `commercial_links`, normalizador e verificação `fmcommand`, `source = fmcommand`, evento de suspensão, reconciliação,
  contingência, modo sombra e testes. **Nenhuma migration é criada nesta PR.**
- **O lançamento não espera o Billing Central:** a Cakto direta foi provada (07/10/2026).
- Risco assumido: durante a transição existem duas vias; o campo `authority` e o modo sombra existem exatamente para impedir efeito duplicado.

## Fases propostas (cada uma exige aprovação)

| Fase | Entrega | Onde |
|---|---|---|
| G0 | Aprovar este ADR e o contrato | Diretor |
| G1 | Command: fonte do produto, emissão de identificadores, API de licenças (sem gateways ainda) | Command |
| G2 | AtendeVendeIA: recebimento `fmcommand` desligado por padrão, vínculo e testes | AtendeVendeIA |
| G3 | Modo sombra com o Command emitindo eventos reais de teste | os dois |
| G4 | Reconciliação e contingência provadas (derrubar o Command de propósito) | os dois |
| G5 | Virada por assinatura, depois adaptadores de gateway no Command | Command |

## Complemento do Diretor (08/10/2026): central financeira de TODOS os SaaS

O FM Command será a central financeira de assinaturas de **todos** os SaaS da FM Tecnologia (Kordena, AtendeVendeIA, Iron Fit, NFCore e produtos futuros).
Funcionamento obrigatório, e como este ADR o cobre:

| # | Complemento | Cobertura |
|---|---|---|
| 1 | Clientes contratam os planos e pagam por gateways autorizados da FM (Cakto, Hotmart, Asaas, Stripe) | Fora do produto. O AtendeVendeIA nunca cobra nem recebe pagamento |
| 2 | Gateways cobram e repassam à conta financeira da FM Tecnologia | Relação FM e provedor. O produto não vê valores líquidos nem a conta bancária |
| 3 | O Command centraliza assinaturas, mensalidades, pagamentos confirmados, renovações, cancelamentos, inadimplência, taxas e conciliação | **Só no Command.** O evento de licença não carrega taxa nem conciliação; traz `payment.amount_cents` apenas informativo. Valor vendido, valor recebido e lucro líquido ficam no Command |
| 4 | O Command determina o estado comercial das licenças e o comunica aos SaaS | Eventos `fmcc.license.v1` e API de licenças (já no contrato) |
| 5 | O AtendeVendeIA não mantém autoridade própria **permanente**; provisiona e aplica licenças | `authority` por assinatura; o destino final é só `fmcommand`; `plans.grace_days` vira reserva e deixa de ser regra |
| 6 | Cakto e Hotmart do AtendeVendeIA continuam durante a migração, sem interromper vendas nem assinaturas existentes | Nada muda até a virada de cada assinatura; o recebimento direto atual não é tocado por este trabalho |
| 7 | Transição gradual, sem duplicidade de cobrança, assinatura ou evento | Seção seguinte |

### Prevenção de duplicidade (cobrança, assinatura e evento)

- **Cobrança:** cada assinatura é cobrada por **um só** gateway e uma só conta, definido pelo Command (`subscription_id` → referência da assinatura no gateway). O Command cria a assinatura no gateway com
  chave de idempotência por cliente, produto, plano e período; não cria uma segunda enquanto houver uma viva. Enquanto uma assinatura for `direct:*`, o Command **não** cobra nem recria a assinatura dela.
- **Assinatura:** uma assinatura nunca tem duas autoridades (`authority` único, ver "Responsável único" acima). Cliente que já assina pela Cakto ou Hotmart é **importado** pelo Command com o `subscription_id`
  ligado à referência existente no gateway, **sem nova cobrança**.
- **Evento:** `event_id` único mais `license_version`; evento de quem não é a autoridade é registrado como `not_authoritative` e não aplicado. Antes da virada, o modo sombra só compara.
- **Reembolso e cancelamento** feitos direto no gateway antes da virada continuam chegando pelo recebimento direto; depois da virada o Command é quem os recebe e comunica.

### Escopo multi-produto e implicação para o Kordena

- O contrato `fmcc.license.v1` é **independente de produto** (`product_code`); Kordena, Iron Fit, NFCore e futuros adotam o mesmo contrato e a mesma API de licenças. Nada nele é específico do AtendeVendeIA além dos valores de `product_code` e `plan_code`.
- **Atenção (achado da auditoria):** o **Kordena já tem a própria cobrança**: provedores de pagamento, política de roteamento e direitos de acesso (`entitlements`) dentro do Kordena, e o Command hoje apenas o comanda e lê.
  Tornar o Command a autoridade também do Kordena é uma **migração própria**, com a mesma lógica de transição (responsável único, modo sombra, virada por assinatura), e exige ADR no Command que **substitua** a regra
  atual dos documentos dele (FMCC-09, seção 6: a autoridade da mutação comercial do Kordena é o catálogo canônico do próprio Kordena). Isto é decisão e trabalho do repositório do Command, fora desta PR.
- Os documentos do Command também afirmam que uma assinatura ativa **não** deve ser contada a partir de fatos "ativou" acumulados, porque um cancelamento posterior deixaria a contagem falsa. Por isso as licenças deste contrato
  carregam o **estado completo e a versão**, em vez de só mudanças.

## Perguntas ainda abertas

1. Formato dos identificadores (UUID versus texto prefixado) e quem os emite além do Command.
2. Confirmar os parâmetros propostos: contingência de 72 h e reconciliação a cada 15 min.
3. Quem aprova a virada de uma assinatura para `fmcommand` e com qual registro.
4. Ordem de migração dos outros produtos (o Kordena exige ADR próprio no Command) e quais gateways (Asaas, Stripe) o Command adota primeiro.
5. Como o Command importa as assinaturas já existentes na Cakto e na Hotmart sem criar nova cobrança.
