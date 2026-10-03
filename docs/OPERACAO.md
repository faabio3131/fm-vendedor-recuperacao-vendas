# Operação

## Variáveis de ambiente (API)

Ver `apps/api/.env.example`. Segredos entram pelo painel do provedor de hospedagem, nunca no git.

## Primeiro ambiente

1. Criar o banco e rodar `scripts/db/bootstrap_roles.sql` com um usuário administrador.
2. `python -m fm_seller.cli gen-key` e guardar a chave em cofre. **Perder a chave = perder as
   credenciais dos clientes** (elas precisariam ser digitadas de novo).
3. `python -m fm_seller.cli migrate` com `FM_DATABASE_ADMIN_URL` (papel `fm_owner`).
4. Subir a API com `FM_DATABASE_URL` (papel `fm_app`).

## Criar um cliente manualmente (alternativa à compra automática)

`python -m fm_seller.cli create-tenant --name "Loja" --email dono@exemplo.com --plan fase-1`

O dono entra com a conta Google desse e-mail; o convite vale 30 dias.

## Compras do próprio SaaS (Cakto/Hotmart da F&M)

1. Defina `FM_PLATFORM_CAKTO_SECRET` e/ou `FM_PLATFORM_HOTMART_HOTTOK` (vazio = recebimento desligado).
2. No painel da plataforma, aponte o webhook para `POST {FM_PUBLIC_BASE_URL}/v1/platform/webhooks/cakto`
   (ou `/hotmart`) com o mesmo segredo.
3. Ligue cada produto a um plano: `python -m fm_seller.cli map-product --provider cakto --product-id ID --plan fase-1`.
4. No produto, configure o link de acesso para a tela de login do painel. O comprador entra com a conta
   Google **do mesmo e-mail da compra**; sem isso o convite não casa.

Compra aprovada cria cliente, plano e convite (30 dias). Atraso → `past_due`; cancelamento e reembolso →
`canceled` (não libera recursos). Produto sem plano mapeado fica guardado (`unmapped_product`) e é aplicado
pelo worker depois do `map-product`. Eventos sem e-mail não são provisionados (`no_email`): ver tabela
`platform_events`. **Os caminhos dos campos e os nomes de evento da Hotmart são palpites tolerantes: capture
um evento real de cada plataforma antes de vender.**

## WhatsApp: ligar o recebimento

1. Em Conexões → WhatsApp oficial, informe ID do número, ID da conta, token e o **segredo do app Meta**
   (sem ele, nenhuma mensagem recebida é aceita).
2. Cole no painel da Meta a URL de webhook exibida e o segredo do webhook como **token de verificação**
   (aparece uma vez, ao salvar). Assine o campo `messages`.
3. A conexão só vira "conectada" depois do teste ou do primeiro evento assinado válido.

Mensagem "SAIR", "PARAR" etc. bloqueia o contato, encerra a recuperação dele e enfileira uma confirmação.
Estado de entrega (enviada/entregue/lida/falhou) vem do WhatsApp. Texto livre só sai dentro de 24 h da última
mensagem do cliente; fora disso só template aprovado.

## Vendedor IA

Desligado por padrão (Vendedor IA → Ajustes). Preço, nome e link de pagamento vêm só das ofertas cadastradas;
resposta do modelo com URL ou valor em reais é recusada e a conversa vai para uma pessoa. Sem oferta ativa, IA
desligada ou modelo indisponível, a conversa também vai para uma pessoa. **Ainda não há adaptador de modelo
real**: em staging/produção, toda conversa nova cai na fila "Aguardando pessoa". O worker (`cli worker`) roda
o vendedor, a fila de saída e a recuperação no mesmo ciclo.

## Worker de recuperação

`python -m fm_seller.cli worker [--interval 30] [--once]` envia os passos devidos e reprocessa eventos que
falharam. Em staging/produção o envio fica **indisponível** (o worker não pega passos) até existir o adaptador
real do WhatsApp Cloud, validado com conta verificada na Meta. Passo cujo resultado de envio é incerto vira
`failed` e **nunca** é reenviado. Passos presos em `sending` por mais de 1 h são marcados `failed`.

Regras aplicadas na hora do envio: recuperação ligada, consentimento declarado, não contatar, janela de
silêncio (21h–8h no fuso do cliente), limite diário por contato, máximo por venda, template aprovado e WhatsApp
conectado. Compra aprovada em até 7 dias após o fim da sequência ainda conta como recuperada
(janela de atribuição: decisão de produto a confirmar).

## Oportunidades (comércio local)

Sem integração: em **Recuperação** o lojista registra uma oportunidade (orçamento, pedido pendente), importa
um CSV (colunas `telefone` obrigatória; `nome`, `produto`, `valor`, `link`, `observacao`; `;`, `,` ou tab; até
200 linhas) ou liga "Recuperar conversas que esfriaram" (padrão desligado; 3 h sem resposta). Cada registro exige
confirmar que o cliente autorizou contato. Reimportar o mesmo telefone+produto+valor não duplica. O worker
precisa estar rodando (a detecção de conversas faz parte do ciclo). Templates `orcamento_1..3` e `conversa_1..2`
também precisam estar aprovados na Meta antes de qualquer envio.

## Rotação da chave de cifragem

Adicionar a nova chave **na frente** em `FM_SECRETS_KEYS` (`novo:base64,antigo:base64`). Valores novos
usam a nova chave; os antigos continuam legíveis. A re-cifragem em lote dos valores antigos ainda
não está implementada.
