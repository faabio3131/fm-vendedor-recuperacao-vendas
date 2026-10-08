# Contrato de licenças: FM Command (Billing Central) para os SaaS da FM Tecnologia (AtendeVendeIA como primeiro consumidor)

Versão do contrato: `fmcc.license.v1` · Escrito em 08/10/2026 · **PROPOSTA**: nada aqui existe ainda do lado do Command nem do AtendeVendeIA.
Decisão e cercas: `docs/adr/0005-billing-central-do-fm-command.md`. Auditoria: `docs/auditoria/AUDITORIA_BILLING_CENTRAL_2026-10-08.md`.

O Command emite licenças; cada SaaS as aplica. **O contrato é independente de produto** (`product_code`): Kordena, AtendeVendeIA, Iron Fit, NFCore e futuros usam o mesmo `fmcc.license.v1`. Este documento descreve o consumo pelo AtendeVendeIA, o primeiro. O contrato tem dois canais: **eventos assinados** (webhook) e **API de licenças** (reconciliação).

## 1. Versionamento

- `schema_version` em todo corpo: `fmcc.license.v1`. Mudança que quebra compatibilidade cria `v2` em rota nova; `v1` continua aceita durante a migração.
- Campos novos opcionais podem ser acrescentados em `v1`; o receptor **ignora campos desconhecidos** e **recusa** versão que não conhece (`422`).

## 2. Identificadores (todos imutáveis, UUIDv7 emitidos pelo Command e transmitidos como texto opaco)

| Campo | Significado |
|---|---|
| `customer_id` | Cliente comercial na FM; **chave canônica**. Nunca é o e-mail |
| `subscription_id` | Assinatura do cliente a um produto |
| `license_id` | Direito de acesso concedido por uma assinatura a um produto |
| `product_code` | Produto (`ATENDEVENDEIA`, `KORDENA`, `IRON`...), definido pelo Command uma só vez; cada receptor recusa produto diferente do seu |
| `event_id` | Identificador único do evento (chave de idempotência) |

O `tenant_id` do AtendeVendeIA **nunca** trafega: o produto mapeia `customer_id`, `subscription_id` e `license_id` para o seu tenant (`commercial_links`).

## 3. Estados da licença

`pending` (paga ainda não confirmada; nada é provisionado) · `active` · `past_due` (em carência) · `suspended` · `canceled` · `refunded` · `expired` (vigência acabou).

| Estado do Command | Estado do AtendeVendeIA (`tenant_plans.status`) |
|---|---|
| `pending` | sem tenant (ou inalterado) |
| `active` | `active` |
| `past_due` | `past_due` (tudo liberado até `grace_ends_at`) |
| `suspended`, `expired` | `suspended` (envios e vendedor IA pausados; nada é apagado) |
| `canceled` | `canceled` |
| `refunded` | `refunded` |

## 4. Corpo do evento (webhook)

`POST /v1/platform/webhooks/fmcommand` (rota da família de compras; desligada com 404 sem `FM_PLATFORM_FMCOMMAND_SECRET`).

```json
{
  "schema_version": "fmcc.license.v1",
  "event_id": "evt_...",
  "type": "license.activated",
  "occurred_at": "2026-10-08T12:00:00Z",
  "issuer": "fmcommand",
  "source": { "kind": "gateway", "gateway": "cakto", "gateway_ref": "<id do pagamento no provedor>" },
  "product_code": "ATENDEVENDEIA",
  "customer_id": "...",
  "subscription_id": "...",
  "license": {
    "license_id": "...",
    "license_version": 7,
    "state": "active",
    "plan_code": "fase-1",
    "valid_from": "2026-10-08T00:00:00Z",
    "valid_until": "2026-11-08T00:00:00Z",
    "grace_ends_at": null,
    "contingency_hours": 72
  },
  "provisioning": {
    "authorized": true,
    "admin_email": "cliente@exemplo.com",
    "admin_name": "Nome do Responsável",
    "account_name": "Loja Exemplo"
  },
  "payment": { "amount_cents": 9790, "currency": "BRL" }
}
```

- `license` é o **estado completo** da licença naquele momento (não um delta): receber um evento fora de ordem não corrompe, porque vale o de maior `license_version`.
- `provisioning` só vai quando o Command **autoriza** criar a conta (diretriz 4); sem `authorized: true`, o AtendeVendeIA não cria tenant, conta, administrador nem convite.
  O e-mail do administrador serve **só** ao convite de acesso; não identifica a licença.
- `source` indica a origem comercial (gateway e referência), para auditoria e conciliação no Command.
- `payment` é informativo. Nunca cartão, CPF, endereço nem credencial.

### Tipos de evento

`license.activated` · `license.renewed` · `license.plan_changed` · `license.past_due` · `license.recovered` · `license.suspended` · `license.canceled` · `license.refunded` · `license.expired`.
O tipo descreve o motivo; **o efeito vem de `license.state`** e da regra de versão (seção 6).

## 5. Autenticação, anti-replay e idempotência

- Cabeçalhos: `X-FMCC-Signature: t=<unix>,kid=<id da chave>,v1=<hex>` e `X-FMCC-Event-Id` (igual a `event_id`).
- `v1 = HMAC-SHA256(segredo[kid], "<t>.<corpo bruto>")`, comparação em tempo constante. Dois segredos podem valer ao mesmo tempo (rotação por `kid`).
- **Replay:** recusa (`401`) se `|agora − t| > 300 s` (mesma tolerância do webhook da Cakto hoje) ou se `event_id` do cabeçalho difere do corpo.
- **Idempotência:** `event_id` único em `platform_events (provider, dedupe_key)`; evento repetido responde `200 {"status":"duplicate"}` sem efeito.
- O segredo de recebimento é **distinto** do token de leitura da API de licenças e do token do ADR-0004. Nunca no repositório; por referência nos dois lados.

## 6. Eventos fora de ordem e versão da licença

- `license_version` cresce a cada mudança de estado da licença, por `license_id`.
- O AtendeVendeIA aplica um evento **só se** `license_version` > a versão já aplicada. Menor ou igual: `stale` (registrado, sem efeito).
- Salto de versão (ex.: de 5 para 8): aplica a mais nova e **dispara reconciliação** daquela licença.
- Transições são sempre validadas contra o estado atual (como em `lifecycle.next_status`); transição impossível fica registrada como `ignored`.

## 7. Responsável único (transição com Cakto e Hotmart)

Cada assinatura tem um `authority` no AtendeVendeIA (`direct:cakto`, `direct:hotmart`, `fmcommand`). Evento `fmcommand` para assinatura com outra autoridade: resultado
`not_authoritative`, registrado e **não aplicado** (e o inverso para eventos diretos depois da virada). **Modo sombra** (`FM_FMCOMMAND_MODE=shadow`): todo evento `fmcommand` é
validado e registrado, nenhum é aplicado.

## 7.1 Gates para transferência de autoridade\n\nAntes da mudança de `authority`, o adaptador do gateway correspondente deve estar operacional e homologado no Command; pagamentos e estornos devem estar reconciliados; os estados comerciais devem coincidir em modo sombra; o Command deve emitir eventos e fornecer autorizações de provisionamento via API. A mudança é por assinatura, com aprovação humana registrada da administração central da FM Tecnologia (ator, justificativa, horário, evidências), idempotência, proteção concorrencial e plano de recuperação governado. Em caso de falha, não permitir simultaneamente duas autoridades de escrita.\n\n## 8. API de licenças do Command (reconciliação)

Somente leitura, `Authorization: Bearer <token>` com escopo `licenses:read` limitado ao produto `ATENDEVENDEIA`; base `https` em origem permitida.

| Rota | Resposta |
|---|---|
| `GET /v1/licenses/{license_id}` | `200` com o objeto `license` completo, `customer_id`, `subscription_id`, `product_code`, `as_of`; `404` se não existir |
| `GET /v1/licenses?product_code=ATENDEVENDEIA&updated_since=<cursor>&limit=100` | `200` `{ "items": [...], "next_cursor": "...", "as_of": "..." }` ordenado por atualização |

- O AtendeVendeIA reconcilia **a cada 15 minutos** (parâmetro aprovado), na subida e depois de salto de versão. Divergência: aplica a licença do Command (maior `license_version`), registra
  `license.reconciled` com o antes e o depois. Em modo sombra, só relata.
- Erros `5xx`, `429` e rede: nova tentativa com espera crescente; `4xx` de credencial gera alerta (não adianta repetir).

## 8.1 Recuperação de provisionamento perdido\n\nA listagem de reconciliação e a consulta individual devem devolver também o estado completo de autorização de provisionamento, incluindo os campos necessários para a criação idempotente do tenant e convite. O SaaS não pode depender exclusivamente do primeiro webhook de ativação. O provisionamento deve ser feito uma única vez por licença/assinatura, com mapeamento estável e proteção contra corrida.\n\n## 9. Indisponibilidade e contingência

- Sem resposta do Command: vale a **última licença validada** (`last_validated_at`).
- Licença dentro de `valid_until` (ou de `grace_ends_at`): segue normal.
- Passou do prazo sem confirmação: **contingência** de no máximo `contingency_hours` (teto fixo na configuração do AtendeVendeIA; o valor do Command nunca excede o teto; teto aprovado: 72 h),
  registrada no início e no fim com `contingency_started_at` e `contingency_until` persistidos de forma durável (nunca reiniciar a janela por restart ou nova tentativa). Só se aplica à última licença previamente ativa ou em carência e não pode reativar um estado explicitamente `suspended`, `canceled`, `refunded` ou `expired`. **Sem prorrogação automática:** ao fim, `suspended`. Só licença nova validada reabre.

## 10. Auditoria

Cada evento gera linha em `platform_events` (corpo bruto cifrado) com `outcome` em `applied`, `duplicate`, `stale`, `ignored`, `not_authoritative`, `unmapped_product`, `unauthorized_provisioning`, `failed`, e registro
em `audit` com o `license_id` como alvo. Reconciliação, divergência, entrada e saída de contingência também. Nenhum registro leva dado de pagamento.

## 11. Respostas do webhook

`200 {"status":"<resultado>"}` · `400` JSON inválido · `401` assinatura, carimbo ou `kid` inválido · `404` rota desligada · `413` corpo grande · `422` `schema_version` ou `product_code` desconhecido ·
`429` limite de falhas por IP. O Command reenvia em rede e `5xx`; `4xx` não adianta repetir.

## 12. Fronteira financeira e multi-produto

- O Command centraliza assinaturas, mensalidades, pagamentos confirmados, renovações, cancelamentos, inadimplência, **taxas e conciliação**. Nada disso é enviado ao produto: o evento de licença
  não carrega taxa, líquido, conta de repasse nem dado do gateway além de `source.gateway` e `gateway_ref` (auditoria). `payment.amount_cents` é informativo.
- Valor vendido, valor recebido e lucro líquido existem **só no Command**.
- O produto **não** cobra, não cria assinatura em gateway e não mantém autoridade permanente sobre o estado comercial. Provisiona (tenant, conta, administrador, convite) e aplica a licença.
- Cliente já assinante pela Cakto ou Hotmart é importado pelo Command com `subscription_id` ligado à referência existente, **sem nova cobrança**; até a virada, o recebimento direto continua sendo a autoridade (campo `authority`).
- Cada assinatura tem um só gateway cobrando e uma só autoridade; ver ADR-0005, "Prevenção de duplicidade".

## 13. O que cada lado precisa construir (nada existe ainda)

**Command** (`FM-CONTROL-CENTER`): emissão de `customer_id`, `subscription_id` e `license_id`; adaptadores de gateway; emissor de webhook assinado com fila e reenvio (o modo `webhook` do
Source Registry hoje é recusado pelo runtime); API de licenças; fonte do AtendeVendeIA no Source Registry (hoje inexistente).

**AtendeVendeIA:** `commercial_links` e `tenant_plans.source = fmcommand`; normalizador e verificação `fmcommand`; evento de suspensão externa; vigência e `license_version`;
reconciliação; contingência; modo sombra; testes de assinatura, replay, idempotência, ordem, transição e isolamento entre clientes.
