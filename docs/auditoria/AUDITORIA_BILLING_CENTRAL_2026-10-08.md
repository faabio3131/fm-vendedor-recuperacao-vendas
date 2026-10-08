# Auditoria prévia à integração com o Billing Central do FM Command

Data: 08/10/2026 · Escopo: **somente leitura e documentação**. Nenhum código, banco, variável ou ambiente foi alterado.
Pedido do Diretor: auditar a estrutura do AtendeVendeIA e verificar o acesso ao `FM-CONTROL-CENTER` antes de definir o contrato, para não criar contratos incompatíveis.

## 1. Acesso ao FM Command

- Repositório `faabio3131/FM-CONTROL-CENTER`: acesso **somente leitura** obtido nesta sessão, clone raso em `main` (`d83098c`, "Merge PR #61: governed platform integrations control plane").
- O repositório estava público por esquecimento e foi tornado **privado** pelo Diretor durante a sessão. A leitura posterior exigiu anexar o repositório com a autorização dele.
- Nada foi escrito no repositório do Command. O que está registrado aqui é leitura de código e documentos dele.

## 2. O que o FM Command tem hoje (relevante para o contrato)

| Tema | Achado | Fonte no repositório dele |
|---|---|---|
| Integração por fonte | Source Registry por tenant, com `sourceType`, `authoritativeDomain`, `syncMode` (`pull`, `webhook`, `hybrid`), `secretRef` (por referência, nunca valor), `mappingVersion`, `freshnessSeconds` | `src/domain/integration/contracts.ts` |
| Fatos canônicos | `ConnectorFact { externalId, factType, payload, sourceTimestamp }`, com idempotência, cursor, provenance e deduplicação por tenant, fonte, `externalId` e versão do mapeamento | idem, `FMCC-07-INTEGRATION-FABRIC-v0.1.md` |
| Modo webhook | **Declarado no tipo, mas não suportado no runtime**: o sync de uma fonte `webhook` lança `ConnectorUnsupportedModeError`. Não há receptor de webhook de gateway nem emissor de webhook para produto | `src/application/integration/connector-runtime.ts` |
| Segurança de origem | `baseUrl` só `https`, sem credencial embutida e dentro de lista de origens permitidas; token de serviço de no mínimo 32 caracteres, resolvido por referência | `kordena-billing-connector.ts` |
| Cobrança do Kordena | Painel que **comanda a API do Kordena** (provedores de pagamento, credenciais, teste, política de roteamento com principal e reserva). Quem tem a lógica de cobrança e os gateways é o Kordena; o Command administra por fora | `kordena-billing-control-service.ts`, `kordena-commercial-connector.ts` |
| Direitos de acesso | O snapshot do Kordena traz `customers`, `subscriptions`, `billing_transactions` e `entitlements`, **dados do Kordena**. O Command não tem entidade própria de cliente comercial, assinatura ou licença | `kordena-commercial-connector.ts` |
| Identificadores | Não existem `customer_id`, `subscription_id` nem `license_id` emitidos pelo Command | busca no código e nos documentos |
| Gateways | Não há adaptador de Cakto, Hotmart, Asaas, Mercado Pago nem Stripe | busca no código |
| AtendeVendeIA | **Não aparece** no Command (nem como produto, nem como fonte). O tenant "Nova FM Tecnologia" tem Kordena, IRON, CampaIA e NFCore; só o Kordena está `CONNECTED` | `docs/governance/FMCC-RUNTIME-INTEGRATION-STATUS-2026-10-06.md` |
| Governança | RBAC por permissão (`billing:read`, `integration:write`), isolamento por tenant no servidor, auditoria e `correlationId` | `src/domain/security/*` |

**Conclusão (Command):** a diretriz aprovada (Billing Central, licenças, adaptadores de gateway, identificadores canônicos, webhook de licença) é **trabalho novo no Command**. O que existe hoje é a base para isso (Source Registry, fatos, segredo por referência, origens permitidas), mas nenhum dos itens do contrato existe ainda.

## 3. O que o AtendeVendeIA tem hoje

| Tema | Achado | Onde |
|---|---|---|
| Compras do próprio SaaS | `POST /v1/platform/webhooks/{provider}` com `provider` em `cakto` ou `hotmart`; sem segredo configurado a rota responde 404 | `api/routes/platform.py`, `provisioning/platform.py` |
| Autenticação | Cakto: HMAC-SHA256 de `"{timestamp}.{corpo}"` com tolerância de 300 s, ou `secret` no corpo; Hotmart: `hottok`. Comparação em tempo constante. Falhas repetidas travam o IP | `events/ingest.py` |
| Idempotência | `platform_events` com `UNIQUE (provider, dedupe_key)`; corpo bruto **cifrado**; `outcome` e `error` por evento; reprocesso de eventos com falha | migration 0002, `provisioning/platform.py` |
| Provisionamento | Compra aprovada cria tenant, `tenant_plans` e `pending_invites` (papel dono, validade de 30 dias) para o **e-mail do comprador**; o login Google desse e-mail aceita o convite. O vínculo cliente↔compra é o **e-mail** (`_find_tenant`) | `provisioning/platform.py` |
| Produto → plano | `plan_products (provider, external_product_id) → plan_key` | migration 0002 |
| Estado comercial | `tenant_plans.status` em `active`, `past_due`, `suspended`, `canceled`, `refunded`; máquina de transições em `lifecycle.next_status`; suspensão por carência calculada (`plans.grace_days`, padrão 3) pelo worker (`enforce_grace`) ou manual na área `/admin` | `provisioning/lifecycle.py`, migration 0009 |
| Efeito do estado | `blocks_service` pausa envios e vendedor IA; nada é apagado ao suspender | `lifecycle.py`, `ai/seller.py`, `recovery/engine.py` |
| Origem do plano | `tenant_plans.source` com restrição `('manual', 'cakto', 'hotmart')` e `external_ref` | migration 0001 |
| Isolamento e privilégio | RLS forçada; papéis `fm_owner` e `fm_app`; processamento de compras roda em modo `system` do banco | migration 0001, `db.py` |
| Auditoria | `audit(...)` para criação de cliente, ativação e mudanças de estado | `services.py` |
| Leitura pelo Command | `GET /v1/control-plane/fmcc/health` e `/snapshot` por token de serviço (só leitura, agregados, desligada por padrão) | ADR-0004, `control_plane.py` |

**Lacunas do AtendeVendeIA frente à diretriz:**

1. A chave de vínculo é o **e-mail** (a diretriz diz: e-mail não é chave primária). Falta mapear `customer_id`, `subscription_id` e `license_id`.
2. `tenant_plans.source` não admite `fmcommand`.
3. Não existe evento de **suspensão** vindo de fora (a suspensão é calculada ou manual), nem **vigência** (`valid_until`) nem **versão de licença** para eventos fora de ordem.
4. A carência está no AtendeVendeIA (`plans.grace_days`); pela diretriz passa ao Command.
5. Não há **reconciliação** periódica nem **contingência** limitada quando a fonte comercial fica indisponível.
6. Não há **responsável único por assinatura**: hoje Cakto e Hotmart agem sozinhas, e a convivência com o Command exigiria evitar duplicidade.
7. A assinatura de webhook usa um formato por provedor; o Command precisará de um formato próprio, com `kid` para rotação de segredo.

## 4. Contratos disponíveis hoje (auditados) e compatibilidade

Pedido do complemento do Diretor: auditar os contratos disponíveis antes de documentar.

| Contrato existente | Dono | Natureza | Relação com o Billing Central |
|---|---|---|---|
| `GET /v1/control-plane/fmcc/health` e `/snapshot` do AtendeVendeIA (`atendevendeia.fmcc.v1`, ADR-0004) | AtendeVendeIA | Leitura agregada pelo Command, token de serviço | **Mantido.** Continua sendo a saúde e a contagem de assinaturas; não vira canal de licença |
| `POST /v1/platform/webhooks/{cakto,hotmart}` | AtendeVendeIA | Entrada de compra direta do gateway | **Preservado na transição** (complemento 6); desligado só no fim, por assinatura |
| `kordena.fmcc.commercial.v1` (snapshot com `customers`, `subscriptions`, `billing_transactions`, `entitlements`, `catalog`) e comandos comerciais governados | Kordena e Command | Pull do Command mais comandos do Command ao Kordena | O Kordena **tem autoridade própria de cobrança**. Migrar o Kordena para o Billing Central é projeto separado (ver ADR-0005) |
| `ConnectorFact` e `SourceDefinition` (`pull`, `webhook`, `hybrid`) | Command | Contrato interno de integração | Base reutilizável para a fonte do AtendeVendeIA; o runtime recusa o modo `webhook` hoje |
| Contratos de IRON, CampaIA e NFCore com o Command | Command | **Inexistentes** (`UNSUPPORTED` no registro de 06/10/2026) | Esses produtos entram direto no contrato `fmcc.license.v1`, sem legado a migrar |

**Compatibilidade verificada com o contrato proposto (`fmcc.license.v1`):**

- **Cabe no modelo do Command:** `externalId` do fato (`event_id` ou `license_id` com versão), `sourceTimestamp` (`occurred_at`), `mappingVersion` (`schema_version`), `secretRef` (segredo por referência) e a lista de origens permitidas (`https`).
- **Corrige uma armadilha documentada por ele:** o Command não conta assinatura ativa a partir de fatos cumulativos; o contrato carrega **estado completo mais versão**, que é o modelo certo para "ativo agora".
- **Nome do produto:** o registro do Command usa `IRON` (o Diretor fala "Iron Fit"); o `product_code` deve ser definido pelo Command e uma só vez.
- **Conflito de regra a resolver no Command:** os documentos dele atribuem a autoridade comercial do Kordena ao catálogo do próprio Kordena. A diretriz nova a inverte. É preciso ADR no Command que a substitua.
- **Duplicidade de cobrança:** o AtendeVendeIA nunca cobra, então o risco de cobrança duplicada está só entre gateways e Command; o ADR-0005 define a regra (um gateway por assinatura, importação sem nova cobrança, idempotência por cliente, produto, plano e período).

## 5. Pontos que exigem decisão ou alinhamento (não bloqueiam a documentação)

- Formato de identificador do Command (UUID versus texto prefixado) e quem os emite.
- Parâmetros de contingência (proposta no ADR: 72 horas) e de reconciliação (proposta: 15 minutos).
- Quem pode mudar o responsável de uma assinatura (Cakto direta para Command) e com qual aprovação.
- Ordem de migração dos produtos e ADR no Command para o Kordena.

## 6. Cercas deste trabalho

- Esta PR só acrescenta documentos. **Sem merge, deploy ou mudança operacional** até aprovação posterior do Diretor.
- O lançamento do AtendeVendeIA segue com Cakto e Hotmart diretas (decisão 7 da diretriz); nada aqui o atrasa.
