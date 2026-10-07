# Staging no Render: passo a passo (modo construção, plano grátis)

Para quem cria o ambiente (Fábio), inclusive pelo celular. Atualizado em 04/10/2026. **Nada aqui foi
validado no Render ainda**: o `render.yaml` é rascunho, e cada passo diz o que conferir. Se algo não bater,
pare e me mande a mensagem de erro (nunca senha, chave ou token: a conversa não é cofre).

## Modo construção: o que o plano grátis permite e o que não

Decisão do Fábio (04/10/2026): enquanto construímos, tudo no grátis, **sem dado real de cliente**. Limites
segundo a documentação do Render (consultada em 04/10/2026):

| Limite | Efeito para nós |
|---|---|
| Serviço web grátis dorme após 15 min sem tráfego; acordar leva cerca de 1 minuto | Primeira abertura do painel é lenta. **Antes de testar um webhook da Cakto/Hotmart, abra `/v1/health` para acordar a API**: a Hotmart desativa o webhook se a URL der erro, e a Cakto espera resposta em 8 s |
| 750 horas grátis por mês por workspace | Dois serviços ligados o mês todo estouram; o Render suspende até o mês seguinte |
| Banco grátis **expira 30 dias após a criação** (14 dias de carência para virar pago), sem backup, um por workspace | Servem para construir. Antes do prazo, ou se torna pago ou se recria. Nada de dado real |
| Sem Shell nos serviços grátis | Por isso existe o `cli bootstrap` (passo 4) em vez de comandos manuais |
| **Não existe plano grátis para worker nem cron** | Ficam fora: sem recuperação enviada, sem sincronização de templates, sem `ops-check`. Servem para testar login, painel e recebimento de webhooks |

Preço quando passar para o pago (página oficial do Render, 04/10/2026): web a partir de US$ 7/mês, Postgres
a partir de US$ 6/mês; com API, painel e worker e o banco, cerca de US$ 27/mês. Conferir no painel antes de
confirmar.

O Render não tem região no Brasil (Oregon, Ohio, Virginia, Frankfurt, Singapore). Os dados ficam fora do
país: levar isso à revisão de LGPD com o advogado (P8).

## Decisão de 05/10/2026: Render grátis só para o staging

O Fábio decidiu usar o Render **grátis apenas como staging** (sem dado real de cliente). A **produção** será em outro provedor com
região em São Paulo, escolhido depois; nessa hora este guia e os blueprints mudam (`docs/render.pago.yaml` é só ponto de partida).
O plano de execução desta fase é `docs/PLANO_FASE_STAGING.md`.

**O que o staging grátis consegue testar, e o que não:**

| Teste (seção 4 de `docs/LANCAMENTO_MVP.md`) | No grátis? | Por quê |
|---|---|---|
| 2 Login Google real | Sim | só API e painel |
| 3 Teste da conexão do WhatsApp (precisa `FM_WHATSAPP_LIVE=true`) | Sim | chamada da API à Meta |
| 4 Webhook de entrada (handshake, assinatura, mensagem recebida, entrega, "SAIR") | Sim, com a API acordada | recebimento é da API; só **a resposta automática** do vendedor depende do worker |
| 5 Templates: enviar para aprovação e ver o status | Parcial | enviar e "Atualizar status" são da API; a sincronização sozinha é do worker |
| 6 Envio de texto e de template, fila de saída | **Não** | a fila é processada pelo worker |
| 7 Compra de teste Cakto/Hotmart e captura de evento | Sim | recebimento é da API; o aplicar de produto sem plano mapeado é do worker |
| 1 Gemini (`ai-check`, `ai-eval --real`) | Sim, **fora do Render** | roda pela linha de comando, em qualquer máquina com a chave |
| Vendedor IA respondendo, recuperação de venda, `ops-check` | **Não** | dependem de worker/cron, que não existem no grátis |

Para testar o que depende do worker é preciso, **com a autorização do Fábio**: plano pago do worker, ou rodar o worker numa máquina própria
contra o banco do staging (só se o banco aceitar conexão de fora; não foi verificado).

**Dois bloqueios conhecidos antes de começar:** o banco grátis é um por workspace e o do Fábio já está ocupado por outro projeto; e o usuário
padrão do banco grátis pode não conseguir criar o papel `fm_app` (o `bootstrap` para e avisa). Se qualquer um ocorrer, **parar** e decidir com o Fábio:
Postgres grátis de outro lugar, ou banco pago (a partir de cerca de US$ 6/mês, a confirmar no painel).

## Banco do staging: Neon (decisão de 05/10/2026)

A vaga do banco grátis do Render está ocupada por outro projeto, então o banco do staging é um **Postgres grátis do Neon**: projeto
`atendevendeia-staging`, região **US East 1 (N. Virginia)**, só o serviço Postgres ligado (Auth, Funções, Armazenamento e Portal de IA desligados).
O `render.yaml` **não cria banco** (o bloco `databases` ficou comentado).

- Use sempre a **conexão direta** (no Neon: "Connect", desligar "Connection pooling"; o endereço **não** tem `-pooler`).
- **Duas URLs, dois usuários. Não são a mesma.** Erro que aconteceu no primeiro deploy: a URL do app foi colada nas duas variáveis.

  | Variável | Usuário | Senha |
  |---|---|---|
  | `FM_DATABASE_ADMIN_URL` | `neondb_owner` (dono; banco `neondb`) | a que o Neon mostra em "Connect" (clicar em mostrar senha) |
  | `FM_DATABASE_URL` | `fm_app` | a do passo 1 (`FM_BOOTSTRAP_APP_PASSWORD`) |

  O restante (endereço direto sem `-pooler`, banco, `?sslmode=require&channel_binding=require`) é igual nas duas. Sintoma de trocar: `password authentication
  failed for user 'fm_app'` já no `bootstrap`, porque o `fm_app` ainda nem existe e o `bootstrap` só usa a `FM_DATABASE_ADMIN_URL`.
- O `bootstrap` cria o papel `fm_app`. **Confirmado em 06/10/2026:** o `neondb_owner` do plano grátis do Neon **pode** criar papéis. Se um banco futuro não
  deixar, o `bootstrap` para e avisa (nunca rodar a API como dono do banco).
- Limites do Neon grátis (busca de 05/10/2026, conferir no site): 1 GB por projeto; computação desliga após 5 min parada e a primeira consulta demora um pouco
  mais; 100 horas de computação por mês por projeto (estourou, suspende até o mês seguinte). Sem garantia de backup: **nada de dado real**.
- O Neon grátis suspende o banco após 5 min parado e **derruba as conexões abertas**. Antes da correção do pool (`db.py`: `check`, `max_idle`, `keepalives`), o primeiro pedido depois da pausa dava 500 (`WARNING psycopg.pool: discarding closed connection` no log); no painel aparecia "Algo deu errado." no login. O plano grátis do Render também dorme a API após 15 min: a primeira visita leva 30 a 50 s e pode dar 502 enquanto ela acorda; tente de novo.
- Painel e API dormem no plano grátis (15 min sem uso). A tela de login agora trata 502, 503, 504 e falha de rede como "servidor acordando": mostra o aviso e tenta de novo até 6 vezes, a cada 10 s (cerca de 1 minuto). Recusa de verdade (401, 403) continua aparecendo na hora. Para testar à mão sem esperar, abra `<api>/v1/health` antes do login.
- A URL e a senha do banco **nunca** vão para o git nem para o chat: só para as variáveis do Render e para o gerenciador de senhas.
- Produção: banco e API em provedor com região em São Paulo, escolhidos depois (os textos jurídicos dizem São Paulo).

## Comando de início da API (correção de 06/10/2026)

O `dockerCommand: sh -c "..."` do primeiro rascunho **falhou no Render**: o log mostrou `sh: 1: python -m fm_seller.cli bootstrap && exec uvicorn ... not found`
e `Exited with status 127` (o comando inteiro foi tratado como nome de programa). O início agora está no `Dockerfile` (`apps/api/start-api.sh`):
com `RUN_BOOTSTRAP_ON_START=1` ele roda o `bootstrap` e depois sobe a API na porta `$PORT`; se o `bootstrap` falhar, a API **não** sobe.
Num serviço que já existe, o campo **Docker Command** (Settings do serviço) foi gravado com o comando antigo: **apague o conteúdo** desse campo e salve, e
adicione a variável `RUN_BOOTSTRAP_ON_START` = `1` em Environment. Depois do merge, o deploy é manual (Auto-Deploy fica desligado ao usar "Deploy a specific
commit"). Se o `Docker Command` ficar com o texto antigo, o erro `Exited with status 127` volta.

## Provar a chave da IA no Render (rota de diagnóstico)

O plano grátis não tem Shell nem processo em segundo plano, e o quadro "Testar conversa" do painel usa só o simulador. Para provar que a chave
`FM_AI_API_KEY` configurada no Render funciona de verdade: entre no painel com a conta de administrador da plataforma, abra `/admin` e clique em
**Testar IA** (seção "Teste da IA"). A rota `POST /v1/admin/ai-check` faz **duas chamadas reais** ao modelo (centavos) com um cliente de exemplo,
**não grava nada, não envia a ninguém e nunca devolve a chave**: só mostra modelo, tempo, tokens e o texto da resposta de teste. Só administrador da
plataforma; no máximo uma verificação a cada 30 s; cada chamada espera no máximo 12 s (o repasse do painel corta em 30 s). Sem chave configurada,
ela responde "Nenhuma chave de IA configurada" e não chama ninguém. Chave do plano gratuito do Google: só com dados inventados (nesse plano o Google
pode usar o conteúdo para melhorar produtos); antes de conversa de cliente de verdade, ative o faturamento na chave.

**Como virar administrador sem Shell (testado em 07/10/2026):** o login só aplica o convite de administrador na próxima entrada. No SQL Editor do
Neon (banco `neondb`, papel dono), rode `BEGIN; SELECT set_config('app.system', 'on', true); INSERT INTO platform_admin_invites (email, expires_at) VALUES
(lower('<seu e-mail do Google>'), now() + interval '2 days'); COMMIT;` (equivale a `create-platform-admin`), depois clique em **Sair** e entre de novo.
**Trocar o modelo no Render:** variável `FM_AI_MODEL` no serviço da API (por exemplo `gemini-3.1-flash-lite`) e Manual Deploy da API; apagar a variável
volta ao padrão do código. Se uma das duas chamadas mostrar `rede: ReadTimeout`, o Google demorou mais que 12 s: clique de novo depois de 30 s.

## O que será criado (região Virginia)

| Recurso | Tipo | Para quê |
|---|---|---|
| (banco no Neon, fora do Render) | Postgres grátis | banco; ver a seção "Banco do staging: Neon" |
| `fm-seller-api` | Web Service Docker (free) | API; no início roda o `bootstrap` |
| `fm-seller-web` | Web Service Docker (free) | painel |

## Passo 1: chave de cifragem e senha do app

Preciso de dois segredos, que você guarda em cofre (nunca no chat nem no git):

- **Chave de cifragem** `FM_SECRETS_KEYS`: o formato é `id:base64` com **exatamente 32 bytes** (44 caracteres em base64, terminando em `=`), por exemplo
  `k1:` seguido do base64. Gerar com `python -m fm_seller.cli gen-key` ou, em qualquer computador, `echo "k1:$(openssl rand -base64 32)"`. Fora desse formato a
  API cai ao iniciar com `Formato inválido em FM_SECRETS_KEYS (use id:base64)`. Colar sem espaço antes ou depois. Perder a chave = perder as credenciais dos
  clientes (guarde uma cópia separada); **não troque a chave depois que houver credenciais guardadas**.
- **Senha do papel do app** `FM_BOOTSTRAP_APP_PASSWORD`: uma senha forte nova, só letras e números
  (símbolos como `@` ou `/` quebram a URL do banco).

## Passo 2: Google (login)

Sem `FM_GOOGLE_CLIENT_ID` a API **não sobe**. Para criar: Google Cloud Console → APIs e serviços →
Credenciais → Criar credenciais → ID do cliente OAuth → Aplicativo da Web. Em **Origens JavaScript
autorizadas** ponha o endereço do painel (passo 4). Em tela de consentimento "Em teste", só e-mails
cadastrados como usuário de teste entram. Se ainda não quiser criar agora, use
`pendente.apps.googleusercontent.com`: a API sobe e o login simplesmente não funciona até você trocar.

## Passo 3: criar pelo Blueprint

Render → New → Blueprint → repositório `faabio3131/fm-vendedor-recuperacao-vendas`, branch `main`. O
Render lê o `render.yaml` e cria o banco, a API e o painel. Ele pede os valores marcados `sync: false`:

| Variável | Valor |
|---|---|
| `FM_DATABASE_ADMIN_URL` | URL **direta** do Neon com o dono do banco (`neondb_owner`); só migrations |
| `FM_DATABASE_URL` | a mesma URL direta, trocando o usuário por `fm_app` e a senha pela do passo 1 |
| `FM_BOOTSTRAP_APP_PASSWORD` | a senha do passo 1 (a mesma usada em `FM_DATABASE_URL`) |
| `FM_BOOTSTRAP_OWNER_EMAIL` | seu Gmail |
| `FM_BOOTSTRAP_TENANT_NAME` | nome do primeiro cliente (ex.: `F&M Teste`) |
| `FM_SECRETS_KEYS` | a chave do passo 1 |
| `FM_GOOGLE_CLIENT_ID` e `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | o Client ID do passo 2 |
| `FM_PUBLIC_BASE_URL` e `API_PROXY_TARGET` | endereço público da API (`https://fm-seller-api….onrender.com`) |
| `FM_WEB_ORIGIN` | endereço público do painel (`https://fm-seller-web….onrender.com`) |
| `FM_PLATFORM_CAKTO_SECRET`, `FM_PLATFORM_HOTMART_HOTTOK`, `FM_AI_API_KEY` | deixe vazio por enquanto |
| `FM_FMCC_CONTROL_PLANE_TOKEN` | token do FM Command (mín. 32 caracteres, `openssl rand -base64 48`); vazio = conexão desligada (`docs/FM_COMMAND_INTEGRACAO.md`) |

Como o banco é criado junto, a URL dele só existe depois: deixe `FM_DATABASE_URL` e
`FM_DATABASE_ADMIN_URL` em branco, crie, copie a URL **Internal** do banco, preencha em *Environment* da
API e faça *Manual Deploy*. O mesmo vale para os endereços da API e do painel. O Render só pergunta os
`sync: false` na criação: variável nova depois entra manualmente. `FM_WHATSAPP_LIVE` fica `false`.

Se o Render **recusar** o arquivo, ele diz a linha. Mande a mensagem que eu corrijo.

## Passo 4: o que o `bootstrap` faz sozinho

A cada subida da API, antes de abrir a porta, o comando `cli bootstrap` (idempotente):

1. cria o papel `fm_app` (sem superusuário e sem bypass de RLS), se ainda não existir;
2. aplica as migrations pendentes;
3. cria o primeiro cliente e o convite para o seu Gmail, só se o banco ainda não tem nenhum cliente.

Se o log mostrar **`O usuário padrão deste banco não pode criar papéis`**, **pare**: não rode a API como
dono do banco (isso desliga o isolamento entre clientes). Me avise: a saída é outro Postgres gerenciado ou
pedir a liberação ao suporte do provedor. No Neon grátis isso **não ocorreu** (06/10/2026).

## Passo 5: conferir (cole aqui só os resultados, nunca segredos)

Antes de abrir o painel, rode o teste de fumaça (ele espera a instância grátis acordar e não manda cookie nem segredo):
`python -m fm_seller.cli smoke --api https://<api> --web https://<painel>`. Deve terminar em `RESULTADO: OK`
(`COM AVISOS` é aceitável em construção; `NÃO SUBA` não). Os itens abaixo continuam valendo à mão, para o que o smoke
não cobre.

1. No log da API, procure `papel fm_app: created`, `migrations: 0001_core.sql, …, 0007_operacao.sql` e
   `primeiro cliente: criado`.
2. `https://<api>/v1/health` → `{"status":"ok",…}` e `https://<api>/v1/ready` → `{"status":"ready"}`.
3. **`https://<painel>/v1/health` deve devolver o mesmo JSON.** Se der 404, o Render não passou
   `API_PROXY_TARGET` ao build do painel e o login não vai funcionar: me avise.
4. `https://<painel>/login` abre.
5. Com o Client ID real e o seu e-mail como usuário de teste, entrar pelo painel.

## Antes da subida de verdade (conferência de ambiente)

Com as variáveis do ambiente (as mesmas que você preencheu no Render) exportadas na sua máquina, ou no Shell do serviço
pago, rode `python -m fm_seller.cli preflight`. `RESULTADO: NÃO SUBA` lista o que corrigir, por nome, sem mostrar
segredo. Ensaie também o rollback do banco com `scripts/ops/rehearsal.sh` (ver `docs/OPERACAO.md`).

## Para sair do modo construção

Antes de qualquer dado real: trocar para os planos pagos (banco e serviços), copiar `docs/render.pago.yaml`
sobre `render.yaml` (traz worker e cron), ligar o `ops-check` e testar backup e restauração
(`docs/OPERACAO.md`). Banco grátis não tem backup.

## O que continua sem prova

**Provado em 06/10/2026** (commit `64441be`, ver `docs/TESTES_REAIS_REGISTRO.md`): `$PORT` e o início pelo `start-api.sh`; criação do papel `fm_app` pelo dono do Neon;
repasse de variável de build ao painel (`/v1` chega à API); `cli smoke` com `RESULTADO: OK`.

**Sem prova:** o Blueprint em si (o serviço existente foi ajustado à mão); login com Google (falta o Client ID); envio real, templates e teste de conexão da
Meta, Gemini, Cakto e Hotmart (dependem das contas, ver `docs/LANCAMENTO_MVP.md`); tudo que precisa de processo em segundo plano (vendedor IA, recuperação, fila).
Nada disso deve ser dado como funcionando antes do teste real.
