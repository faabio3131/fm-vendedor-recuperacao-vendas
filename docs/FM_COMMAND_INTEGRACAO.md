# Integração com o FM Command (centro de controle da F&M)

Escrito em 05/10/2026. O FM Command é o centro de controle do Fábio (repositório `FM-CONTROL-CENTER`, domínio `fmcommand.com.br`, nome técnico FMCC).
Este documento descreve **o lado do AtendeVendeIA**, já construído e testado, e **o que o FM Command precisa implementar** do lado dele.
Decisão e cercas: `docs/adr/0004-conexao-com-o-fm-command.md`.

> **Atualização de 08/10/2026:** o FM Command será também o Billing Central (assinaturas e licenças de todos os SaaS). Proposta de como o AtendeVendeIA recebe
> licenças dele: `docs/adr/0005-billing-central-do-fm-command.md`, `docs/FM_COMMAND_LICENCAS.md` e a auditoria `docs/auditoria/AUDITORIA_BILLING_CENTRAL_2026-10-08.md`. Esta página continua valendo para a **leitura** (saúde e snapshot).

> **Estado honesto:** o lado do AtendeVendeIA está pronto e testado contra o contrato. O lado do FM Command **não existe** (não foi alterado, porque o
> repositório dele só foi lido). Nenhuma chamada real entre os dois foi feita. Nada aqui é "conectado" até o item 7 abaixo passar com evidência.

## Como o FM Command integra produtos (lido do repositório dele)

Fluxo dele: fonte de verdade, Integration Fabric, fatos canônicos, Metric Registry, Metric Engine, inteligência, Core. Um conector implementa
`health()` e `pull()`; a fonte tem `secretRef` (por referência, nunca valor bruto), `sourceType`, `baseUrl` (só `https`, com lista de origens
permitidas) e modo `pull`. A execução tem timeout, retry em 429 e 5xx, idempotência, cursor e deduplicação de fatos por
`tenant + fonte + externalId + versão do mapeamento`. Ausência não é zero. O conector do Kordena é o exemplo
(`src/infrastructure/integration/kordena-commercial-connector.ts`).

## Contrato do AtendeVendeIA

Base: `https://<api do AtendeVendeIA>`. Todas as chamadas levam `Authorization: Bearer <token>` e podem levar `x-correlation-id` (vira o
`x-request-id` da resposta e do log). Só `GET`.

| Rota | Resposta |
|---|---|
| `GET /v1/control-plane/fmcc/health` | `200` `{"schema_version":"atendevendeia.fmcc.v1","product_code":"ATENDEVENDEIA","status":"ok","findings":[{"level","code"}]}`; `503` com `status` `degraded` (achado crítico do `ops-check`) ou `database_unavailable`. Só códigos, sem dado de cliente |
| `GET /v1/control-plane/fmcc/snapshot` | `200` com o corpo abaixo |

Códigos de erro: `404` conexão desligada · `401` token ausente, curto ou errado · `429` muitas falhas (trava por IP) · `503` saúde.

```json
{
  "schema_version": "atendevendeia.fmcc.v1",
  "product_code": "ATENDEVENDEIA",
  "as_of": "2026-10-05T12:00:00+00:00",
  "summary": {
    "customers": 3, "active_subscriptions": 2, "past_due_subscriptions": 0,
    "suspended_subscriptions": 0, "canceled_subscriptions": 1,
    "refunded_subscriptions": 0, "deletion_pending": 0
  },
  "facts": [
    {"external_id": "sub:<uuid do cliente>:active", "fact_type": "subscription.active",
     "payload": {"tenant_id": "<uuid>", "plan_key": "fase-1", "status": "active", "product_code": "ATENDEVENDEIA"},
     "source_timestamp": "2026-10-01T10:00:00+00:00"}
  ],
  "operations": {
    "worker": {"cycles": 0, "last_cycle_age_s": null, "last_error": false},
    "outbox": {"queued": 0, "oldest_queued_age_s": null, "sent_24h": 0, "failed_24h": 0},
    "recovery": {"steps_overdue": 0, "steps_sent_24h": 0, "steps_failed_24h": 0},
    "events": {"received_24h": 0, "failed_24h": 0, "platform_failed": 0},
    "ai": {"replies_today": 0, "failures_today": 0}
  },
  "coverage": {"subscriptions": "available: ...", "billing_invoices": "unavailable: ..."}
}
```

Notas do contrato:
- `facts[]` tem o mesmo formato do `ConnectorFact` do FM Command (`external_id`, `fact_type`, `payload`, `source_timestamp`). Hoje só
  `subscription.active` e `subscription.cancelled`, que batem com as métricas `subscription.active.count` e `subscription.cancelled.count` do
  Metric Registry dele. Atraso, suspensão e reembolso aparecem só em `summary`, não viram fato (a semântica é decisão do FM Command).
- `operations` é **modelo de leitura**, não fato canônico (regra dele: observabilidade não é fato do Metric Engine). Serve para a tela de saúde.
- `coverage` diz por domínio `available`, `partial` ou `unavailable`: `trials`, `billing_invoices`, `payments`, `receivables`,
  `infrastructure_cost`, `operating_cost`, `leads`, `support` e `internal_test_tenants` são **indisponíveis** aqui. Não preencher com zero.
- O `snapshot` é o estado completo agora (como o do Kordena), o `pull` pode usar `as_of` como cursor.
- Privacidade: nenhum e-mail, telefone, nome de cliente, conversa ou credencial. Os `tenant_id` são UUIDs opacos.

## Ligar do lado do AtendeVendeIA (variável de ambiente)

1. Gere um valor aleatório de pelo menos 32 caracteres (por exemplo `openssl rand -base64 48`) e guarde em cofre. **Nunca no git nem no chat.**
2. Coloque em `FM_FMCC_CONTROL_PLANE_TOKEN` na API do AtendeVendeIA (variável do provedor). Sem ela a conexão fica desligada (404).
3. Reinicie a API. O `cli smoke` não cobre estas rotas; teste com o token: `/v1/control-plane/fmcc/health` deve dar `200` ou `503` com `status`.
4. Para trocar o token: mude nos dois lados e reinicie. Para desligar: apague a variável.

## O que o FM Command precisa implementar (no repositório dele, com a autorização do Fábio)

Proposta, **não verificada** com o dono do FM Command (nomes de variáveis são sugestão):

1. **Conector `atendevendeia-v1`**, copiando o desenho do `kordena-commercial-connector.ts`: `health()` chama `/v1/control-plane/fmcc/health`;
   `pull()` chama `/snapshot`, valida `schema_version`, `product_code` e a forma dos fatos, e falha fechado se o contrato mudar. `503` no `health`
   vira `degraded`; 429 e 5xx já são tratados como transitórios pelo Connector Runtime dele.
2. **Segurança**, como a do Kordena: tenant de controle dedicado, lista de origens `https` permitidas, token por referência (`env:...`), nunca em
   configuração bruta nem no navegador.
3. **Produto** `atendevendeia` no Product Registry dele e a fonte ligada a esse produto, para o escopo por produto funcionar.
4. **Fatos para métricas:** `subscription.active` e `subscription.cancelled` já mapeiam; o resto do `coverage` deve aparecer como "indisponível".
5. **Semântica a aprovar por ele:** trial (não existe aqui), inadimplência (`past_due` só em `summary`), receita (da Cakto/Hotmart, não daqui).
6. **Testes dele**, incluindo token inválido, origem fora da lista, contrato quebrado (não pode virar zero) e isolamento de tenant.
7. **Prova de conexão real** (depende de os dois lados estarem no ar, com HTTPS): `health` verde, `snapshot` com fatos, `capture`/sync registrados.

## O que NÃO está confirmado

- Nenhuma chamada real FM Command para AtendeVendeIA. Só testes do lado do AtendeVendeIA contra o contrato.
- Como o Render entrega o IP do FM Command e se o proxy preserva o cabeçalho `Authorization` (conferir no staging).
- O staging grátis do Render dorme após 15 min: a primeira chamada pode demorar cerca de 1 min (o conector do FM Command tem timeout).
- Contagem de clientes de teste: não há marca de "cliente interno/teste", então no staging todos contam em `summary` e nos fatos.
- O `snapshot` lê todas as linhas de plano de uma vez: serve para dezenas ou centenas de clientes; para milhares seria preciso paginar.
