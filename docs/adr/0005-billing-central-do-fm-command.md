# ADR-0005 — Billing Central do FM Command: o AtendeVendeIA obedece à licença

Status: **PROPOSTA** (08/10/2026), aguarda o Diretor e o alinhamento com o repositório do FM Command · Data: 2026-10-08

## Contexto

Direção dada pelo Fábio em 08/10/2026 (arquitetura prevista, **ainda não conferida tecnicamente** no repositório do FM Command, que esta sessão não leu):

- O **FM Command** será a central financeira e comercial de todos os SaaS da F&M Tecnologia (assinaturas, pagamentos, renovações, inadimplência,
  faturamento), como **Billing Central**.
- O Command **não é o gateway** de pagamento. Ele administra e integra gateways (Cakto, Asaas, Mercado Pago, Stripe ou outro) por uma camada
  *Payment Gateway Adapter*, valida o pagamento por webhook autenticado mais consulta à API do provedor e **determina o direito de acesso** (licença).
- Cada SaaS (Kordena, Iron Fit, AtendeVendeIA, NFCore) consulta ou recebe a licença do Command; **nenhum SaaS tem integração financeira própria**.
- O Command separa **valor vendido**, **valor recebido** e **lucro líquido**, conciliando taxas, estornos e impostos.

Hoje (ADR-0004 e `docs/FM_COMMAND_INTEGRACAO.md`) o Command só **lê** o AtendeVendeIA. E a Cakto/Hotmart avisam o AtendeVendeIA **direto**
(`/v1/platform/webhooks/{provedor}`), com a política de atraso nossa (`plans.grace_days`, 3 dias).

## Decisão (proposta)

1. **Novo remetente `fmcommand`** na mesma rota de compras do SaaS (`POST /v1/platform/webhooks/fmcommand`), com **eventos de licença** (contrato em
   `docs/FM_COMMAND_LICENCAS.md`). O AtendeVendeIA aplica a licença; não fala com gateway.
2. **Cakto e Hotmart continuam funcionando** como estão. Os dois caminhos convivem; quando o Billing Central estiver homologado, os webhooks diretos
   são desligados (apagar o segredo do provedor desliga o recebimento).
3. **Uma só fonte de verdade para a tolerância de atraso.** Com o `fmcommand`, quem decide é o Command: ele manda `license.past_due` com
   `grace_until` e depois `license.suspended`. Os 3 dias do AtendeVendeIA (`plans.grace_days`) ficam só como **reserva** para evento sem `grace_until`.
4. **Autenticação:** assinatura HMAC-SHA256 do corpo com segredo próprio (`FM_PLATFORM_FMCOMMAND_SECRET`, mínimo 32 caracteres), carimbo de tempo com
   janela curta contra reenvio, comparação em tempo constante. Sem segredo configurado a rota responde 404 (desligada por padrão), como a Cakto.
5. **Idempotência** pelo `event_id` do Command (tabela `platform_events`, já existente). Ordem de chegada não é garantida: cada evento só muda o estado
   nas transições previstas em `lifecycle.next_status`; o resto fica registrado como ignorado.
6. **Identificação do cliente:** o **e-mail do comprador** (como hoje) liga a licença à conta; o `customer.external_id` do Command é guardado para
   auditoria e para a consulta periódica. Plano: `plan_products(provider='fmcommand', external_product_id=<plan_code>)` → `plan_key`.
7. **Conferência periódica (fase 2, opcional):** o AtendeVendeIA consulta o Command (`GET` de licença por cliente, com token de serviço) para
   corrigir aviso perdido. Só depois de o Command oferecer essa rota.
8. **Fora do escopo do AtendeVendeIA:** gateways, conciliação, taxas, impostos e lucro líquido. O evento traz `payment.amount_cents` só como
   informação; o AtendeVendeIA não calcula receita.

## Consequências

- **Nada disto está implementado nem testado contra o FM Command.** Este ADR e o contrato são a base para o Command implementar do lado dele.
- Falta no código do AtendeVendeIA: o normalizador `fmcommand`, o segredo, o evento de **suspensão** (hoje a suspensão vem do cálculo da carência ou da
  área `/admin`, não de um evento) e os testes. Sem mudança de banco prevista (usa `plan_products`, `platform_events`, `tenant_plans`).
- **Lançamento não espera o Billing Central:** a compra na Cakto já foi provada com compra real (07/10/2026). Plugar o Command depois não muda nada
  para o cliente.
- Dado pessoal no evento: só e-mail, nome e valor. Nunca cartão, CPF ou dado de pagamento do cliente.

## Perguntas em aberto (para o Diretor e o FM Command)

1. Formato real que o Command consegue enviar (campos, cabeçalho de assinatura, nomes de evento). O contrato aqui é uma **proposta**.
2. Chave do cliente: e-mail do comprador, ou código próprio do Command?
3. Quem cria a conta e o convite após a compra: o AtendeVendeIA ao receber `license.activated` (como hoje) ou o Command?
4. O Command terá a rota de consulta de licença (item 7)?
