# FM Command ↔ FM SaaS Connector Standard V1 — proposta arquitetural

Estado: DESIGN PROPOSTO, não implementado nem autoridade produtiva.
Não bloquear o lançamento comercial inicial do AtendeVendeIA por este trabalho.

## Padrão para TODOS os SaaS da FM Tecnologia

1. Cada produto fornece um `Product Manifest` versionado (product_code imutável,
   API base, capabilities implementadas, tenants externos, scopes, versions e
   cobertura de métricas). Registro feito uma vez no Command; descoberta e
   onboarding governados, não adaptação manual por produto.
2. `Service Identity` provisionada pelo Command com credencial curta/rotativa
   e scopes de leitura/escrita mínimos. Verificação mútua de emissor, audiência,
   assinatura e revogação; segredo fora do repositório e do browser.
3. `Telemetry & Usage Contract`: eventos versionados e agregados por tenant
   (message.receive, ai.usage, token.usage, ai.cost, integration.status,
   worker.health, error, trial, license); idempotency_key, occurred_at,
   schema_version, trace_id, product_code e tenant_id canônico/mapeado.
   PII/texto de conversa não saem por padrão; sem fonte, marcar unavailable.
4. `Commercial Authority Contract`: Command é futuro dono de catálogo,
   preços, checkout, assinatura e licença; SaaS apenas valida licença e
   aplica os entitlements/quotas canônicos no servidor. Fail-closed se
   autorização expirar, com contingência limitada e documentada.
5. `Commands Contract`: suspender/reativar, mudar plano, atualizar franquia,
   revogar, provisionar tenant; cada comando autenticado, versionado, auditado,
   com idempotência, aprovação para ações críticas, assinatura, sequência e
   confirmação. Nunca confiar no header `X-Tenant-ID` vindo do navegador.
6. `Sync/Reconciliation`: snapshot paginado e deltas/outbox com cursor,
   retries/backoff/DLQ, reconciliação e alertas de divergência. Não aceitar
   duas autoridades simultâneas de escrita.
7. `Connector SDK & Conformance Suite`: biblioteca de contratos,
   OpenAPI/JSON Schema, testes de isolamento tenant, RBAC, replay, assinatura,
   quotas, mudanças de versão e revogação. Novo SaaS implementa adaptador
   mínimo e passa certificação, sem alterar código do Command.

## Migração inicial do AtendeVendeIA
LOCAL_CONTROL → SHADOW (Command lê, sem escrever) → RECONCILE → COMMAND_AUTHORITY.
Cada transição exige evidência, rollback e aprovação; não recriar planos já
vendidos, não trocar IDs de clientes ou enviar licenças duplicadas.
