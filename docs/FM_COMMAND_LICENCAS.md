# Contrato proposto: eventos de licença do FM Command para o AtendeVendeIA

Escrito em 08/10/2026. **PROPOSTA**: nada aqui existe do lado do FM Command nem do AtendeVendeIA (decisão e cercas: `docs/adr/0005-billing-central-do-fm-command.md`).
O AtendeVendeIA aplica a licença que o Command determina; não fala com gateway.

## Rota

`POST /v1/platform/webhooks/fmcommand` (mesma família das compras da Cakto/Hotmart; desligada, 404, sem `FM_PLATFORM_FMCOMMAND_SECRET`).

## Autenticação

Cabeçalho `X-FMCC-Signature: t=<unix>,v1=<hex>`, onde `v1 = HMAC-SHA256(segredo, "<t>.<corpo bruto>")`. Recusa (401) se a assinatura não bate ou se
`t` estiver a mais de 5 minutos do relógio do servidor. Segredo compartilhado, guardado por referência nos dois lados, nunca no repositório.

## Corpo

```json
{
  "schema_version": "fmcc.license.v1",
  "event_id": "evt_01J...",
  "type": "license.activated",
  "occurred_at": "2026-10-08T12:00:00Z",
  "product_code": "ATENDEVENDEIA",
  "plan_code": "fase-1",
  "customer": { "external_id": "cus_123", "email": "cliente@exemplo.com", "name": "Loja Exemplo" },
  "subscription": { "external_id": "sub_456", "period_end": "2026-11-08T00:00:00Z", "grace_until": null },
  "payment": { "gateway": "cakto", "amount_cents": 9790, "currency": "BRL" }
}
```

`event_id` é a chave de idempotência. `grace_until` só vai em `license.past_due`. `payment` é informativo (o AtendeVendeIA não calcula receita).
Nenhum dado de cartão, CPF ou endereço.

## Eventos e efeito no AtendeVendeIA

| `type` | Efeito | Estado resultante |
|---|---|---|
| `license.activated` | Cliente novo: cria conta, plano e convite para o e-mail. Cliente existente: reativa e troca o plano | `active` |
| `license.renewed` | Renovação paga: confirma o plano e a data do período | `active` |
| `license.past_due` | Atraso; tudo continua liberado até `grace_until` (sem ele, vale `plans.grace_days`) | `past_due` |
| `license.recovered` | Pagamento regularizado | `active` |
| `license.suspended` | Fim da tolerância: envios e vendedor IA pausados, nada é apagado | `suspended` |
| `license.canceled` | Cancelamento | `canceled` |
| `license.refunded` | Reembolso ou estorno | `refunded` |

Eventos fora de ordem ou repetidos: ignorados quando a transição não existe (`lifecycle.next_status`); o evento fica registrado em `platform_events`.

## Respostas

`200 {"status": "<resultado>"}` com resultado em `tenant_created`, `plan_updated`, `plan_status_changed`, `plan_status_unchanged`, `duplicate`,
`ignored`, `unmapped_product`, `tenant_not_found`, `no_email`. `401` assinatura ou carimbo inválido. `404` rota desligada. `413` corpo grande. `400` JSON inválido.
O Command deve reenviar em erro de rede ou `5xx`; `4xx` não adianta repetir.

## O que falta no AtendeVendeIA para isto funcionar

1. Normalizador `fmcommand` (hoje só `cakto` e `hotmart` em `events/normalize.py`) e o segredo na configuração.
2. Evento de **suspensão** (hoje a suspensão vem da carência calculada ou do `/admin`).
3. Cadastro do plano: `plan_products` com `provider='fmcommand'` e `external_product_id` = `plan_code`.
4. Testes de assinatura, idempotência, ordem e transições.

## Fase 2 (opcional): conferência periódica

O AtendeVendeIA consulta a licença de cada cliente no Command (token de serviço, só leitura) e corrige aviso perdido. Depende de o Command
oferecer a rota.
