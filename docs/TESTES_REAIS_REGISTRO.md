# Registro de testes com contas reais (AtendeVendeIA)

Fonte: seção 4 de `docs/LANCAMENTO_MVP.md`; plano em `docs/PLANO_FASE_STAGING.md`. **Só se registra resultado que aconteceu.** Cada linha:
data, ambiente, o que foi feito, o que voltou (copie só o que não é segredo), decisão. O repositório é público: **nunca** cole chave, token, senha,
URL real de ambiente, e-mail ou telefone reais. Estado de cada teste: `NÃO FEITO` · `PASSOU` · `FALHOU` · `PASSOU COM RESSALVA`.

## Ensaio local (item A3 do plano), 05/10/2026

Tudo em Postgres 16 local, sem conta e sem servidor externo. Prova o roteiro de comandos, **não** o Render.

| Passo | Resultado |
|---|---|
| `cli bootstrap` em banco novo no formato do Render (um dono, `fm_app` criado pelo comando) | OK: papel `fm_app` existente, migrations 0001 a 0012 aplicadas, primeiro cliente criado |
| `cli preflight` em dev | `COM AVISOS` (chave do Gemini, segredos Cakto/Hotmart, Client ID provisório, limite de taxa desligado), sem crítico |
| `cli preflight` em staging com senhas de desenvolvimento | `NÃO SUBA`, listou `FM_DATABASE_URL` e `FM_DATABASE_ADMIN_URL` por nome. **Antes da correção da PR #36 quebrava com traceback**; agora imprime o relatório e não ecoa valores |
| `cli smoke --api http://localhost:8000` | `RESULTADO: OK` (saúde, cabeçalhos de segurança, prontidão do banco, `/v1/me` exige login, CORS, guarda de origem, webhooks recusam sem segredo) |
| `scripts/ops/rehearsal.sh` | `ENSAIO OK`: backup, subida da migration 0012, rollback pelo backup (RLS conferida), nova subida |

Limites do ensaio: o dono do banco local é superusuário (no Render não será); `smoke` não rodou contra o painel (`--web`); nada disso prova o repasse de
variáveis de build, `$PORT` nem a criação do papel `fm_app` pelo usuário padrão do Render.

## Staging no Render (item B1 do plano), 06/10/2026

API e painel no Render grátis (Virginia), banco no Neon grátis (N. Virginia), commit `64441be`. Sem dado real, sem conta de terceiros, sem chave de IA.
Prova a subida e o encanamento, **não** os testes com contas reais (tabela abaixo continua `NÃO FEITO`).

| Passo | Resultado |
|---|---|
| Subida da API com `RUN_BOOTSTRAP_ON_START=1` (PR #41) | `bootstrap` executou antes da API; o status 127 do `dockerCommand` com aspas acabou |
| Papel `fm_app` criado pelo dono do Neon (`neondb_owner`) | OK: o plano grátis do Neon **deixa** o dono criar o papel; a API roda como `fm_app`, sem bypass de RLS |
| Migrations | 0001 a 0012 aplicadas (`migrations: já em dia` nas subidas seguintes); primeiro cliente já existia |
| `cli smoke --api <api> --web <painel>` | `RESULTADO: OK`, 11 verificações: saúde; cabeçalhos de segurança; descrição da API não exposta; `/v1/ready` com banco; `/v1/me` exige login; CORS recusa origem estranha; escrita de outra origem recusada; webhooks recusam sem conexão ou segredo; painel abre o login; painel com cabeçalhos de segurança; painel repassa `/v1` até a API |
| `<painel>/login` e `<painel>/v1/health` | HTTP 200; o segundo devolve `{"status":"ok","version":"0.1.0"}`, igual à API |

Tropeços do caminho (todos de configuração, nenhum de código além da PR #41): (1) o campo "Docker Command" do serviço guardava o comando antigo e
precisou ser apagado; (2) `FM_DATABASE_ADMIN_URL` estava com o usuário do app (`fm_app`) em vez do dono, e o Neon recusou a senha de um usuário que ainda
não existia; (3) `FM_SECRETS_KEYS` estava fora do formato `id:base64` e a API caiu ao iniciar. Ver `docs/STAGING_RENDER.md`.

Limites: o login com Google **não** foi testado (falta o Client ID, item B2); vendedor IA, recuperação, fila e sincronização de templates **não rodam** no plano
grátis (sem processo em segundo plano); serviços dormem após 15 min parados e a primeira visita leva cerca de 50 s; a senha do dono do Neon foi exposta em
conversa privada e deve ser trocada ao fim do staging; o plano não prova nada sobre o provedor de produção (a escolher, com região em São Paulo).

## Os 9 testes da seção 4 de `LANCAMENTO_MVP.md`

| # | Teste | Como provar (de `LANCAMENTO_MVP.md`) | Onde roda | Estado | Data | Resultado real | Decisão |
|---|---|---|---|---|---|---|---|
| 1 | Gemini | `ai-check` termina com `RESULTADO: OK`; `ai-eval --real` sem falha de segurança; anotar nome do modelo, formato e custo por resposta | qualquer máquina com a chave (pago, centavos) | NÃO FEITO | | | |
| 2 | Login Google real | entrar com conta Google; e-mail não verificado é recusado | staging | NÃO FEITO | | | |
| 3 | Conexão do WhatsApp | com `FM_WHATSAPP_LIVE=true`, "Testar conexão" confirma token, número e conta; token errado falha sem vazar | staging | NÃO FEITO | | | |
| 4 | Webhook de entrada (WhatsApp, Messenger, Instagram) | handshake, assinatura, mensagem recebida, entrega, "SAIR"; no Messenger/Instagram, formato de envio, ID de quem escreve e janela de 24 h | staging (API acordada); resposta automática precisa de worker | NÃO FEITO | | | |
| 5 | Templates | enviar `pix_1` pelo painel, conferir status, categoria e motivo de reprovação (atenção a variável no início/fim) | staging; sincronização automática precisa de worker | NÃO FEITO | | | |
| 6 | Envio | texto dentro de 24 h e template fora, com parâmetros; passo vira `sent` só com id devolvido; erro incerto vira `failed` sem reenvio | precisa de worker | NÃO FEITO | | | |
| 7 | Compra de teste Cakto/Hotmart | cria cliente, plano e convite; capturar evento real (`FM_CAPTURE_EVENTS=true`); conferir `auth_method`, campos, ciclo de assinatura | staging | NÃO FEITO | | | |
| 8 | Relatório | valor e data do relatório batem com o que a plataforma registrou (D6) | depende de 6 e 7 | NÃO FEITO | | | |
| 9 | Ponta a ponta | compra, login, conectar WhatsApp, template aprovado, oportunidade, mensagem recebida, resposta encerra o caso | precisa de worker | NÃO FEITO | | | |

## Roteiros para criar as contas (para o Fábio)

Telas do Google e da Meta mudam: **confira na tela atual**. Se algo não bater, mande o texto do erro, **nunca** a chave. Guarde chaves em cofre
(gerenciador de senhas) e coloque-as só nas variáveis de ambiente do provedor.

### Google: Client ID do login (teste 2)
1. Google Cloud Console, criar um projeto (por exemplo "AtendeVendeIA staging").
2. Tela de consentimento OAuth: tipo Externo, em modo "Em teste"; adicionar o seu Gmail como usuário de teste.
3. Credenciais, criar "ID do cliente OAuth", tipo "Aplicativo da Web". Em "Origens JavaScript autorizadas", colocar o endereço do painel do staging.
4. Copiar o **Client ID** (não é segredo, mas não vai para o repositório). Vai em `FM_GOOGLE_CLIENT_ID` e `NEXT_PUBLIC_GOOGLE_CLIENT_ID`.
5. Não cole nada além do Client ID no chat.

### Gemini: chave de API (teste 1)
1. Em ai.google.dev (Google AI Studio), criar uma chave de API. **É secreta e paga por uso**: defina um limite de gasto se a tela permitir.
2. Guardar em cofre. Vai só em `FM_AI_API_KEY` (e na linha de comando local para o `ai-check`).
3. Rodar `python -m fm_seller.cli ai-check` e depois `ai-eval --real`; registrar acima o resultado e o custo por resposta.

### Meta: conta de teste do WhatsApp, Messenger e Instagram (testes 3 a 6)
1. Em developers.facebook.com, criar um app do tipo Business e adicionar o produto WhatsApp. A Meta fornece um número de teste e um token temporário.
2. Anotar (em cofre): ID do número, ID da conta do WhatsApp Business, token e o **segredo do app**.
3. Cadastrar na central de conexões do painel; colar a URL de callback e o token de verificação que o painel mostra na configuração do webhook da Meta; assinar o campo `messages`.
4. Para Messenger e Instagram: uma Página do Facebook de teste e uma conta profissional do Instagram ligadas ao app; mesmo cadastro na central de conexões.
5. **Não use número nem contato de cliente.** Só números de teste ou os seus. O que a Meta exige para sair do modo de teste é **a verificar** na documentação atual.

### Cakto e Hotmart: produto de teste (teste 7)
1. Criar um produto de teste (assinatura de preço mínimo, ou o modo teste da plataforma) e apontar o webhook para a URL do painel (`/v1/platform/webhooks/cakto` ou `/hotmart`).
2. Segredo do webhook (Cakto) e Hottok (Hotmart) vão só nas variáveis do provedor.
3. Acordar a API (`/v1/health`) antes da compra de teste; ligar `FM_CAPTURE_EVENTS=true` e desligar ao terminar.
4. Para a Hotmart, se conseguir, cole aqui a página de documentação de webhook (texto público) ou um evento já anonimizado.
