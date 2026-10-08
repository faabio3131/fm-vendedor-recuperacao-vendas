# Licenças do FM Command — implementação no AtendeVendeIA

Lado AtendeVendeIA da Billing Central. **Tudo desligado por padrão** (`FM_FMCOMMAND_MODE=off`).
Contrato de referência: `fmcc.license.v1` (PR de documentação #58 + `FM-CONTROL-CENTER` PR #62).
O Command **ainda não é operacional**: nada aqui foi ligado nem testado contra o Command real.

## Princípios respeitados
- Cakto e Hotmart diretos **intactos** (rotas, credenciais, checkout, ativação). Só se acrescentou `POST /v1/platform/webhooks/fmcommand`, rota própria e anterior à genérica.
- Nenhuma escrita no banco do Command; o Command só é **lido** (GET, https, sem redirecionamento).
- Sem credenciais no código; segredos só por variável de ambiente; payloads guardados cifrados.
- RLS forçada + política só-sistema nas tabelas novas; trilha de auditoria em `audit_log`.
- Nunca duas autoridades: o `enforce_grace` da Cakto/Hotmart ignora quem está sob o Command.
- Migração `0013` é **aditiva** e só roda no `migrate` do deploy (precisa de aprovação para aplicar em banco real).

## Variáveis (todas `FM_`)
| Variável | Padrão | Função |
|---|---|---|
| `FMCOMMAND_MODE` | `off` | `off` ignora tudo · `shadow` só compara e audita · `enforce` aplica |
| `FMCOMMAND_PRODUCT_CODE` | — | código canônico do catálogo do Command |
| `FMCOMMAND_WEBHOOK_SECRETS` | — | `kid:segredo,kid2:segredo2` (rotação por `kid`) |
| `FMCOMMAND_API_BASE_URL` / `_API_TOKEN` | — | API de licenças (https; token ≥ 32 car.) |
| `FMCOMMAND_RECONCILE_MINUTES` | 15 | intervalo (1–60) |
| `FMCOMMAND_CONTINGENCY_HOURS` | 72 | teto (≤ 72) |
| `FMCOMMAND_STALE_AFTER_MINUTES` | 45 | alerta de reconciliação parada |

## Componentes
| Prioridade | Componente | Estado |
|---|---|---|
| 1 vínculo seguro | `commercial_links` (UUIDv7 customer/subscription/license, únicos por emissor) | implementado e testado |
| 2 eventos | assinatura `X-FMCC-Signature` (HMAC-SHA256, 300 s, `kid`), `event_id` idempotente, versão monotônica, isolamento por tenant | implementado e testado |
| 3 estados | pending/active/past_due/suspended/canceled/refunded/expired → status do plano | implementado e testado |
| 4 reconciliação | `GET /v1/licenses` paginado a cada 15 min, atômico (item inválido rejeita a resposta) | implementado e testado com Command simulado |
| 5 contingência | janela ≤ 72 h, persistida, por versão, sem renovação automática, esgotada = bloqueio | implementado e testado |
| 6 shadow | grava divergências sem alterar licença real | implementado e testado |
| 7 provisionamento | conta + convite recuperáveis pela API mesmo sem webhook; sem duplicar | implementado e testado |
| 8 testes | duplicado, fora de ordem, indisponibilidade, replay, cross-tenant, assinatura inválida | 160 testes novos |
| autoridade | `adopt`/`release` com aprovação humana registrada (quem + evidência) | implementado e testado (código); CLI só para `release` |
| operação | achados no `ops-check`; CLI `license-status`, `license-reconcile`, `license-release` | implementado e testado |

## Contrato aprovado × implementação
Estados, tipos de evento (tipo deve concordar com o estado), ids UUIDv7, `license_version` monotônica, `valid_from < valid_until`, `grace_ends_at` só em `past_due` e ≥ `valid_until`, assinatura e janela de 300 s, `X-FMCC-Event-Id == event_id`, lista paginada com `provisioning` e `source`: **conformes**.
Ajustes em relação à doc #58 (a doc deve ser alinhada): `event_id` tratado como string opaca; sem campo `contingency_hours` no contrato (é política local); autoridade `direct|fmcommand`; referência `external_subscription_ref`.

## Pendências no FM Command (bloqueios)
Tabelas/migrations, geração de UUIDv7, API `GET /v1/licenses` com escopo por produto/token, emissor/assinador de webhooks `fmcc.license.v1`, fila de entrega, receptor de webhooks Cakto/Hotmart, importação de assinaturas existentes por referência externa, registro do AtendeVendeIA no catálogo. Sem isso, o modo `shadow`/`enforce` não pode ser ligado.

## Não iniciado / fora de escopo
Migração de qualquer assinatura real; `license-adopt` pela CLI (depende de endpoint de consulta por licença no Command); deploy; merge.
