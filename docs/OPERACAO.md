# Operação

## Variáveis de ambiente (API)

Ver `apps/api/.env.example`. Segredos entram pelo painel do provedor de hospedagem, nunca no git.

## Primeiro ambiente

Staging no Render: siga `docs/STAGING_RENDER.md` (banco com um usuário só usa `scripts/db/bootstrap_app_role.sql`).
Os passos abaixo valem para um Postgres onde você cria os dois papéis.

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
desligada ou modelo indisponível, a conversa também vai para uma pessoa. O worker (`cli worker`) roda
o vendedor, a fila de saída e a recuperação no mesmo ciclo.

**Modelo (Gemini, chave da plataforma).** Defina `FM_AI_API_KEY` na API **e no worker** (nunca no
repositório); `FM_AI_MODEL` (padrão `gemini-3.8-flash`), `FM_AI_BASE_URL` e `FM_AI_TIMEOUT_SECONDS` são
opcionais. Sem chave: dev usa simulador e staging/produção deixam toda conversa na fila "Aguardando pessoa".
Em `FM_ENV=test` o simulador é sempre usado. **Antes de ligar para clientes rode
`python -m fm_seller.cli ai-check`**: faz duas chamadas reais (pergunta de preço e tentativa de burlar as
regras), mostra o texto bruto e o final e termina com `RESULTADO: OK` ou `FALHOU`. O nome do modelo, o
formato de resposta e o preço por mensagem **ainda não foram confirmados contra a API real**.
Falha do modelo (rede, cota, bloqueio, formato inválido) passa a conversa para uma pessoa
(`erro_do_modelo`) e avisa o cliente; não há nova tentativa infinita. O corpo das conversas e a chave
não vão para o log; só modelo, tempo e contagem de tokens.

## WhatsApp real (Meta)

Desligado por padrão. `FM_WHATSAPP_LIVE=true` (API **e** worker) troca o simulador pelo envio real, o teste
da conexão passa a consultar a Graph API (número e conta, só leitura) e liga o envio/sincronização de templates;
`FM_META_GRAPH_VERSION` (padrão `v26.0`), `FM_META_GRAPH_BASE` e `FM_META_TIMEOUT_SECONDS` são opcionais.
**Só ligue depois de validar com uma conta real** (docs/PENDENCIAS_EXTERNAS.md): o formato foi escrito pela
documentação da Meta e testado só contra servidor falso. Sem a variável, staging/produção não pegam passos de
recuperação e o envio de templates responde "ainda não está habilitado".
Fluxo do cliente: Recuperação → Mensagens → "Enviar para aprovação na Meta" (precisa do WhatsApp conectado e
testado) → "Atualizar status" (o worker também consulta sozinho). O nome na Meta é `chave_vN`; editar o texto
volta o template a rascunho e o próximo envio cria a versão seguinte. Os botões "Marcar: …" continuam
para quem aprovou direto no painel da Meta, mas no envio real o template precisa ter sido enviado por aqui
(sem nome na Meta o envio é recusado). **Risco conhecido:** a Meta pode recusar texto que começa ou termina com
variável (os textos padrão terminam com `{link}`); o motivo aparece no template e basta ajustar o texto.

## Limites e custo

- **IA por plano:** `plans.limits` (JSON, dado editável) tem `ai_replies_per_month`; ausente = sem limite. Os
  valores atuais (1000) são **provisórios**, decisão comercial do Fábio. Ao atingir o limite, novas conversas
  vão para uma pessoa (`limite_do_plano`); o painel (Vendedor IA) mostra o uso e avisa a partir de 80%.
  Só chamadas bem-sucedidas contam; falhas ficam registradas à parte. Mês no fuso do cliente. A tabela
  `ai_usage` guarda só contagens e tokens, nunca texto de conversa. Para mudar um plano:
  `UPDATE plans SET limits = '{"ai_replies_per_month": 3000}' WHERE key = 'fase-1';`
- **Contatos novos por dia no número:** a Meta limita quantos contatos novos o número pode abordar em 24 h
  (começa em 250). O cliente ajusta em Recuperação → Ajustes (padrão 200). Passos além do limite são adiados
  para a manhã seguinte (`limite_do_numero`); quem já foi contatado no dia não conta de novo.

## Worker de recuperação

`python -m fm_seller.cli worker [--interval 30] [--once]` envia os passos devidos e reprocessa eventos que
falharam. Em staging/produção o envio fica **indisponível** (o worker não pega passos) até existir o adaptador
real do WhatsApp Cloud, validado com conta verificada na Meta. Passo cujo resultado de envio é incerto vira
`failed` e **nunca** é reenviado. Passos presos em `sending` por mais de 1 h são marcados `failed`.

Regras aplicadas na hora do envio: recuperação ligada, consentimento declarado, não contatar, janela de
silêncio (21h–8h no fuso do cliente), limite diário por contato, máximo por venda, template aprovado e WhatsApp
conectado. Compra aprovada em até 5 dias após o fim da sequência ainda conta como recuperada
(janela de atribuição decidida pelo Diretor em 04/10/2026; constante `ATTRIBUTION_DAYS`).

## Oportunidades (comércio local)

Sem integração: em **Recuperação** o lojista registra uma oportunidade (orçamento, pedido pendente), importa
um CSV (colunas `telefone` obrigatória; `nome`, `produto`, `valor`, `link`, `observacao`; `;`, `,` ou tab; até
200 linhas) ou liga "Recuperar conversas que esfriaram" (padrão desligado; 3 h sem resposta). Cada registro exige
confirmar que o cliente autorizou contato. Reimportar o mesmo telefone+produto+valor não duplica. O worker
precisa estar rodando (a detecção de conversas faz parte do ciclo). Templates `orcamento_1..3` e `conversa_1..2`
também precisam estar aprovados na Meta antes de qualquer envio.

## Alertas (ops-check)

`python -m fm_seller.cli ops-check` lê o banco e imprime o que está parado ou falhando. Código de saída:
**0** em ordem, **1** só avisos, **2** crítico (também se houver migration pendente). Agende a cada 5 minutos
(`render.yaml` tem um cron de rascunho) e ligue a falha do job a um e-mail ou ao WhatsApp de quem opera.

| Achado | Nível | Quando |
|---|---|---|
| `worker_nunca_rodou` / `worker_parado` | crítico | sem ciclo concluído (nunca, ou há mais de 5 min; o normal é 30 s) |
| `passos_atrasados` | crítico | passo de recuperação devido há mais de 15 min |
| `fila_saida_parada` | crítico | mensagem na fila de saída há mais de 10 min |
| `migrations_pendentes` | crítico | o código tem migration que o banco não tem |
| `worker_com_erro` | aviso | o último ciclo do worker falhou (só o tipo do erro; detalhe no log) |
| `falhas_de_envio` | aviso | 5 ou mais falhas em 24 h e pelo menos 20% das tentativas |
| `eventos_falhos` | aviso | evento de entrada (webhook ou compra) falhando há mais de 15 min, mesmo com novas tentativas |
| `sync_templates_parado` | aviso | template aguardando a Meta sem sincronizar há mais de 30 min |

Com envio desligado (staging sem `FM_WHATSAPP_LIVE`) os achados de passo e fila não são avaliados: o acúmulo é
esperado. Um ciclo do worker com erro não derruba o processo; se os ciclos continuarem falhando, o horário do
último ciclo bom envelhece e vira `worker_parado`.

**Não coberto aqui:** API fora do ar e webhook que deixou de chegar (sem vendas e webhook quebrado se parecem
no banco). Para isso, use uma verificação externa em `/v1/ready` e confira a entrega do webhook no painel da
Meta e da Cakto/Hotmart.

## Backup e restauração

- `scripts/ops/backup.sh` gera um dump (`pg_dump`, formato custom, permissão 600) e confere que o arquivo é
  legível. Precisa de `FM_DATABASE_ADMIN_URL` (papel `fm_owner`) e do cliente do Postgres 16 na máquina que
  roda. **Não roda dentro da imagem da API** (ela não tem o cliente nem os scripts): use uma máquina ou job à
  parte, e guarde o arquivo **fora** do servidor do banco.
- Como a RLS é forçada até para o dono, o `pg_dump` comum falha. O script lê em modo sistema e **recusa rodar**
  se alguma tabela com RLS não puder ser lida por inteiro desse modo (exceção declarada: `sessions`, que não
  vai no backup; todos entram de novo pelo Google depois de uma restauração). Tabela nova com RLS precisa de
  uma política que libere `app_system()`, senão o backup para com a mensagem do motivo.
- O dump leva as credenciais dos clientes **cifradas**. A chave `FM_SECRETS_KEYS` **não** vai no backup: guarde
  em cofre separado, com cópia separada. Sem ela o banco restaura, mas as credenciais não abrem.
- `scripts/ops/restore_check.sh arquivo.dump` restaura em um banco **vazio e separado** (`RESTORE_DATABASE_URL`)
  e confere: restauração sem erro, migrations presentes, RLS ativada e forçada em toda tabela com `tenant_id` e,
  com `SOURCE_DATABASE_URL`, contagens iguais às da origem. Repita o teste ao menos uma vez por mês e depois de
  qualquer mudança de provedor.
- Restauração de verdade: criar o banco novo, rodar `bootstrap_roles.sql`, `pg_restore --no-owner
  --enable-row-security` com o papel `fm_owner` (como o `restore_check.sh` faz), `cli migrate` e subir a API
  com a mesma `FM_SECRETS_KEYS`.
- O backup automático do provedor (se o plano tiver) é um segundo seguro, não substitui este: confirmar no
  painel do provedor o que o plano de fato cobre.

## Rollback

- **Código:** voltar para a imagem anterior no provedor (redeploy da versão anterior). Vale para API, worker e
  painel.
- **Banco:** as migrations só andam para frente e o checksum impede editar uma já aplicada. Por isso toda
  migration precisa ser compatível com a versão anterior do código (acrescentar coluna/tabela, nunca remover
  ou renomear na mesma entrega); assim o rollback de código funciona sem desfazer o banco. Desfazer o banco só
  é possível restaurando o backup (perde o que entrou depois dele).
- Antes de qualquer migration em produção: gerar o backup e conferir que ele restaura.

## Rotação da chave de cifragem

Adicionar a nova chave **na frente** em `FM_SECRETS_KEYS` (`novo:base64,antigo:base64`). Valores novos
usam a nova chave; os antigos continuam legíveis. A re-cifragem em lote dos valores antigos ainda
não está implementada.
