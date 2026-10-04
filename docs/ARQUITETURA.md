# Arquitetura

Fluxo: `Painel web → API → Serviços (núcleo) → Portas → Infraestrutura`.

```
apps/web (Next.js)  ──HTTPS+cookie──▶  apps/api (FastAPI, /v1)
                                         ├─ api/        rotas finas
                                         ├─ services.py regras: login, sessão, planos, conexões
                                         ├─ auth/       porta do verificador Google (real + simulado)
                                         ├─ providers/  catálogo de provedores + testadores (porta)
                                         ├─ security/   cifragem AES-256-GCM
                                         └─ db.py       contexto de isolamento por transação
                                                         │
                                          Postgres 16 (RLS forçada, papel fm_app sem bypass)
```

## Isolamento por cliente

Cada transação define `app.tenant_id` e `app.user_id` com `set_config(..., true)` (valem só na
transação). As políticas RLS leem essas variáveis. Modo `app.system` é usado por rotinas de
plataforma (CLI, e futuramente processamento de webhooks) e não é alcançável por rota de usuário.

## Credenciais de clientes

Guardadas em `connections.config_encrypted` (AES-256-GCM). O AAD é `tenant_id|provider`: copiar o
valor para outro cliente ou provedor não decifra. Só o final do segredo vai para `config_hint`.
Chaves em `FM_SECRETS_KEYS` (`id:base64`, a primeira é a ativa; as outras só decifram).

## Planos como dado

`plans.features` lista os recursos. `tenant_plans` liga o cliente ao plano. O catálogo de
provedores exige um recurso por provedor. Plano cancelado não libera nada. Subir de fase é
trocar o plano do cliente.

## Eventos e recuperação (Bloco 2)

`webhook → ingest (segredo, dedupe, payload cifrado) → normalize → handle_event → caso + passos agendados →
worker (checagens na hora do envio) → MessageSender`. O evento bruto é gravado antes de qualquer decisão;
falha de tratamento deixa o evento `failed` para reprocesso. O worker usa o modo `system` do banco
(rotina de plataforma), nunca em rota de usuário. Compras do próprio SaaS entram por rota separada
(`/v1/platform/webhooks/*`) e criam cliente/plano/convite de forma idempotente (lock por e-mail).

## Conversas e vendedor IA (Bloco 3)

`WhatsApp (assinado) → conversa/mensagem (dedupe pelo id do provedor) → worker: vendedor IA → fila de saída →
MessageSender.send_text`. O vendedor responde numa transação que também marca a mensagem como tratada, e a
saída segue a regra de no máximo uma vez. O modelo recebe só persona, ofertas e histórico; nunca credenciais.

## Limites (Bloco 6)

Limite de uso da IA é dado do plano (`plans.limits`), checado antes de cada chamada ao modelo; uso diário
agregado em `ai_usage`. O limite de contatos novos por número é por cliente (`tenant_settings`) e é
reconferido na hora do envio, junto das demais travas da recuperação.

## Modelo de IA (Bloco 5)

`ai/gemini.py` implementa `AiModel` com saída JSON estruturada. O prompt de sistema traz regras fixas, as
ofertas (id, nome, descrição; **sem preço nem link**) e a persona do cliente como mero tom de voz; as
falas do cliente entram só como turnos de usuário. A resposta é validada (campos, tipos, `finishReason`) e
ainda passa por `render_reply`, que recusa URL, valor em reais e marcador sem oferta. Qualquer falha vira
transferência para pessoa. A chave é da plataforma (custo entra no preço do plano); uso por cliente ainda
não é medido.

## Oportunidades próprias (Bloco 4)

O motor não depende de checkout. `Opportunity` (gatilho, origem, referência, contato, produto, valor) é a
entrada única de `open_opportunity`, usada por: eventos de checkout (`source=checkout`), conversa que
esfriou (`conversa`, detectada pelo worker em `recovery/cold.py`), registro avulso (`manual`) e planilha CSV
(`importacao`). Todas passam pelas mesmas regras (consentimento, opt-out, janela de silêncio, limites,
template aprovado). A conversa que volta a escrever encerra o caso `conversa`; o lojista encerra qualquer
caso com "vendido" (conta como recuperada só se alguma mensagem já saiu) ou "perdi". Cakto e Hotmart do
cliente são apenas mais uma fonte; a Cakto/Hotmart da F&M servem só para vender o SaaS (compra cria a conta).

## WhatsApp real e templates (Bloco 7)

`channels/meta_api.py` é o cliente mínimo da Graph API (versão em `FM_META_GRAPH_VERSION`, token só no
cabeçalho). Recusa clara da Meta = `MetaRejected` (nada foi feito); rede, 5xx ou 2xx sem id = incerto, e o motor
marca "falhou" sem reenviar (no máximo uma vez). `WhatsAppCloudSender` envia template (`meta_name`, idioma e
parâmetros na ordem das variáveis) ou texto (janela de 24 h). Os textos do cliente usam `{nome}` `{produto}`
`{valor}` `{link}`; `recovery/templates.py` converte para `{{1}}`… com exemplos. Cada envio à Meta usa o nome
`chave_vN` (texto novo = versão nova; nome não se reutiliza). `recovery/template_sync.py`: envio em 3 passos
(reserva o nome; chamada fora de transação; grava resultado), mapeamento de status
(APPROVED/PENDING/REJECTED/PAUSED/DISABLED) e rotina do worker (5 min enquanto há template aguardando, 30 min
depois, para perceber pausa/desativação). O adaptador real só é escolhido com `FM_WHATSAPP_LIVE=true` e nunca
em `FM_ENV=test`.

## Messenger e Instagram (Bloco 11)

- Contato de rede social não tem telefone nem e-mail: é `contacts.channel_only` com o ID do canal em
  `contact_channels` (PSID no Messenger, IGSID no Instagram), isolado por cliente com RLS forçada. A conversa
  guarda o `channel` (`whatsapp`, `messenger`, `instagram`).
- Entrada (`channels/social.py`): mesma regra do WhatsApp: segredo do app obrigatório, assinatura
  `X-Hub-Signature-256`, mensagem repetida não duplica, eco das nossas mensagens ignorado, entrega e leitura
  vêm do provedor e nunca andam para trás. O registro da mensagem, o opt-out e a reabertura da conversa são
  comuns a todos os canais (`channels/inbound.py`).
- Bloqueio de contato: a identidade em `suppressions` é `canal:id` (no WhatsApp continua o telefone).
- Saída: a fila é uma só (`channels/outbox.py`), com remetente por canal. Um ID de rede social **nunca** passa
  pelo remetente do WhatsApp; sem remetente para o canal a mensagem fica na fila ou falha com motivo.
  Janela de 24 h, no máximo uma vez e resultado incerto vira falha, como no WhatsApp. Estes canais não têm
  templates: a recuperação por template e a detecção de conversa fria seguem só no WhatsApp.
- Vendedor IA e painel de Conversas funcionam igual nos três canais; o painel mostra o canal e mascara o ID.

## Ciclo de vida da assinatura (Bloco 12)

`provisioning/lifecycle.py`: uma tabela de transições (`next_status`) decide o que cada evento de plataforma faz em
cada estado; o que não está na tabela é ignorado, o que torna evento repetido ou fora de ordem inofensivo. Estados
`suspended`, `canceled` e `refunded` não liberam recursos (`tenant_features`) e pausam a fila de saída, os passos
de recuperação, a detecção de conversa fria e o vendedor IA (`blocks_service`). A carência é dado
(`plans.grace_days`) e o worker aplica a suspensão (`enforce_grace`). Suspender **não** muda `tenants.status`
(que impediria o login): o cliente entra, vê "Meu plano" e nada é apagado. `GET /v1/plan` alimenta a tela.

## Relatórios de recuperação (Bloco 13)

`recovery/reports.py` e `GET /v1/reports/recovery` (+ `.csv`): funil por período no fuso do cliente (casos abertos,
com mensagem, enviadas, entregues, lidas, responderam, recuperadas, valor recuperado, "comprou sem mensagem"),
agrupável por dia, produto, sequência ou origem, mais as conversas por canal. Os números vêm só do que o motor
gravou: "recuperada" é o estado do caso pela regra D6 (5 dias, último toque, mensagem enviada, mesmo produto), que o
relatório **não reinterpreta**; entrega e leitura vêm do estado informado pelo provedor; "respondeu" é o contato ter
escrito no WhatsApp depois da primeira mensagem enviada e até o fim do caso. Tudo passa pela RLS do cliente. A
planilha escapa texto que uma planilha leria como fórmula (`=`, `+`, `-`, `@`). Mesmo recurso de plano da
recuperação: plano suspenso, cancelado ou reembolsado não vê o relatório.

## Captura de eventos reais (Bloco 14)

`events/capture.py`: com `FM_CAPTURE_EVENTS=true` (desligado por padrão) cada evento de checkout que **passou na prova
de origem** é guardado cifrado em `event_captures` (do cliente, ou da plataforma com `tenant_id` nulo, só visível ao
modo sistema por RLS). Segredos (`secret`, `hottok`, tokens, senhas) são redigidos **antes** de gravar; o cabeçalho
do hottok, `Authorization` e cookies nunca são guardados; só cabeçalhos úteis (`content-type`, `user-agent`,
`x-cakto-*`, `x-hotmart-*` sem segredo). Expira em `FM_CAPTURE_TTL_HOURS` (72) e guarda no máximo `FM_CAPTURE_KEEP`
(100) por origem; o worker apaga o vencido. Falha na captura nunca derruba o recebimento. `auth_method` registra qual
prova funcionou (`assinatura_hmac`, `segredo_no_corpo`, `hottok_cabecalho`, `hottok_corpo`): responde de graça à
dúvida da chave do HMAC da Cakto. `events/compare.py` confere o evento contra o normalizador usando só caminhos e
tipos (nunca valores): nome conhecido, cada campo achado e onde, o que o evento traz e ninguém lê, e os problemas.
`mask` esconde dados pessoais ao exibir; `anonymize` troca por valores de exemplo válidos para virar fixture.

## Prontidão de subida (Bloco 15)

`ops/preflight.py` (`cli preflight`) confere o ambiente que a API vai usar: a validação de staging/produção, chave de
cifragem, Client ID provisório, usuários de banco distintos, papel do app sem superusuário e sem bypass de RLS, migrations
pendentes e RLS forçada, com saída 0/1/2 e sem imprimir segredo (só motivo e nome do campo). `ops/smoke.py` (`cli smoke`)
testa um ambiente no ar só pelo lado de fora (saúde, prontidão, `/v1/me` fechado, CORS, guarda de origem, webhooks sem
segredo, login do painel e repasse de `/v1`). `scripts/ops/rehearsal.sh` ensaia backup, subida, rollback pelo backup e nova
subida em Postgres local (`migrate --until` aplica só até uma migration). Os blueprints do Render são conferidos por
teste contra o código: toda variável `FM_*` existe em `Settings` e no `.env.example`, segredos nunca são literais, os
comandos existem na CLI, o plano grátis não tem worker nem cron e o pago tem os dois.

## Primeiros passos (Bloco 16)

`onboarding.py` e `GET /v1/onboarding` (qualquer papel, só leitura, na transação do cliente: a RLS restringe tudo):
cada passo (WhatsApp conectado e **testado**, oferta ativa, consentimento, mensagem aprovada, tom de voz, plataforma de
vendas, vendedor IA ligado, recuperação ligada) é calculado de uma consulta ao estado real, nada é marcado à mão, então
a lista não envelhece. Os passos trazem o motivo, o que fazer e a tela certa; `blocks` diz o que cada um impede de ligar.
Os serviços usam as mesmas funções para **barrar o "ligar"**: recuperação sem WhatsApp conectado e vendedor IA sem oferta
ativa respondem 400 `setup_incomplete` com o que falta (consentimento continua com a mensagem própria
`consent_required`). A trava vale só na passagem de desligado para ligado: quem já está ligado pode ajustar e desligar
mesmo que uma conexão tenha caído depois. Conexão apenas salva ("aguardando teste") não conta.

## Portas (para os próximos blocos)

`GoogleVerifier`, `ConnectionTester`, `MessageSender`, `AiModel` e, a seguir, canais de mensagem, IA, checkout e pagamento.
Cada porta tem versão simulada para dev/teste e adaptador real separado.
