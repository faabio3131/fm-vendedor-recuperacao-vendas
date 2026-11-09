# Pedidos e pagamentos dos lojistas — especificação de segurança

## Autoridade e custódia
A FM Tecnologia não recebe, retém nem redistribui o dinheiro das vendas.
Cada estabelecimento configura sua conta Asaas (sandbox ou produção), com sua
própria chave API e token independente de webhook. Credenciais são cifradas
com AES-GCM usando tenant e provedor como dados associados (AAD). Nenhum CPF,
CVV, número de cartão ou segredo vai para o banco de pedidos.

Somente proprietário/administrador autenticado, com plano elegível, cria
pedidos, conecta o gateway, emite cobranças ou pede conciliação. O bot não
recebe credenciais, não decide valores e não aprova pagamento.

## Estado financeiro
- Pedido: pending, charging, awaiting_payment, paid, refunded, disputed,
  review, canceled.
- Preço: valor em centavos vindo de ofertas ativas, snapshot imutável; a taxa
  de entrega é configurada pela loja e aplicada no backend. Limite interno
  R$ 100.000 por pedido e quantidades limitadas por item.
- Pedido possui idempotency key + hash do conteúdo; mesma chave e conteúdo
  devolvem pedido prévio, enquanto a mesma chave com dados diferentes falha.
- Uma cobrança por pedido, com reference av-[UUID]. A reserva local ocorre
  antes da chamada externa. Em falha ambígua, bloquear reenvio e consultar
  cobrança por externalReference. Nunca tentar cobrar novamente às cegas.
- O webhook usa header asaas-access-token validado em tempo constante. Cada
  evento é persistido com ID único, tipo, ID da cobrança e SHA-256 do payload.
  A notificação não é prova de pagamento; o worker consulta o Asaas por API
  com as credenciais do estabelecimento e verifica ID, referência e valor.
- CONFIRMED marca pago, mas não dinheiro disponível; RECEIVED confirma
  liquidação; REFUNDED marca estorno; chargeback marca disputa; parcial ou
  inconsistência exige revisão. Estado financeiro terminal não retrocede.
- Webhooks são at least once. Worker usa lease com retomada e tarefas de
  conferência, sem retry cego de cobrança externa. Não há envio de dinheiro
  para FM Tecnologia nem mecanismo de split neste módulo.

## Ativação e limites
A implementação começa com Asaas como adaptador transacional real. Mercado
Pago, PagBank e outros provedores NÃO estão implementados nesta PR.
O novo painel permite emitir o pedido e copiar o link individual; o bot de
WhatsApp continua no fluxo anterior até uma integração conversacional
determinística de carrinho, adicionais, endereço e autorização do pagador.
O novo módulo não emite nota fiscal nem baixa estoque. Não prometer essas
funcionalidades como concluídas.

Antes do go-live, integrar a PR com CI completa verde, executar a migration
0025_merchant_orders.sql com backup e restauração isolada, testar RLS e
configurar conta Asaas da pizzaria. Registrar no Asaas o webhook autenticado
com o mesmo token independente cadastrado no painel. Verificar sandbox real
e depois um pagamento real expressamente autorizado pelo lojista.
A PR #142 usa a migration 0024_recovery_followups.sql, e deve ser reconciliada
antes da promoção do novo conjunto para a produção.

Documentação oficial:
- https://docs.asaas.com/reference/criar-nova-cobranca
- https://docs.asaas.com/docs/webhook-para-cobrancas
- https://docs.asaas.com/docs/receba-eventos-do-asaas-no-seu-endpoint-de-webhook
- https://docs.asaas.com/reference/recuperar-informacoes-de-pagamento-de-uma-cobranca
