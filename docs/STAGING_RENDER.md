# Staging no Render: passo a passo

Para quem cria o ambiente (Fábio). Atualizado em 04/10/2026. **Nada aqui foi validado no Render ainda**:
o `render.yaml` é rascunho, e cada passo diz o que conferir. Se algo não bater, pare e me mande a mensagem
de erro (nunca senha, chave ou token: a conversa não é cofre).

## O que será criado (todos na região Virginia)

| Recurso | Tipo no Render | Para quê |
|---|---|---|
| `fm-seller-db` | Postgres | banco |
| `fm-seller-api` | Web Service (Docker) | API; roda as migrations antes de subir |
| `fm-seller-worker` | Background Worker | envia a recuperação, sincroniza templates, bate o batimento |
| `fm-seller-web` | Web Service (Docker) | painel |
| `fm-seller-ops-check` | Cron Job | alertas a cada 5 minutos |

O Render não tem região no Brasil (Oregon, Ohio, Virginia, Frankfurt, Singapore). Os dados ficam fora do
país: levar isso à revisão de LGPD com o advogado (P8). Confira o preço de cada recurso no painel antes de
confirmar; não tenho como ver o seu plano.

## Passo 1: banco

1. Render → New → PostgreSQL. Nome `fm-seller-db`, região **Virginia**, database `fm_seller`, plano pago
   menor (o `free` expira). Anote em cofre (não no chat): a URL **Internal** e a **External**.
2. Crie o papel do app, rodando no seu computador com a URL **External** do usuário padrão do Render:

   ```bash
   psql "<URL External do usuário padrão>" -v app_pw="<senha forte NOVA>" -f scripts/db/bootstrap_app_role.sql
   ```

   O resultado deve mostrar `fm_app | f | f` (não é superusuário e não ignora RLS).
3. Se aparecer **`permission denied to create role`**, **pare**: o usuário padrão do Render não pode criar
   papéis. Não rode a API como dono do banco (isso desliga o isolamento entre clientes). Me avise: a saída é
   outro Postgres gerenciado ou pedir a liberação ao suporte do Render.

## Passo 2: chave de cifragem

```bash
echo "k1:$(openssl rand -base64 32)"
```

Guarde o resultado em cofre, **com uma cópia separada**. Perder a chave = perder as credenciais dos
clientes. Ela vai em `FM_SECRETS_KEYS` e nunca no git nem no chat.

## Passo 3: Google (login)

Sem `FM_GOOGLE_CLIENT_ID` a API **não sobe** em staging. Para criar: Google Cloud Console → APIs e serviços
→ Credenciais → Criar credenciais → ID do cliente OAuth → Aplicativo da Web. Em **Origens JavaScript
autorizadas** ponha o endereço do painel (passo 4). Em tela de consentimento "Em teste", só e-mails
cadastrados como usuário de teste conseguem entrar. Se ainda não quiser criar agora, use
`pendente.apps.googleusercontent.com`: a API sobe e o login simplesmente não funciona até você trocar.

## Passo 4: serviços (Blueprint)

Render → New → Blueprint → repositório `faabio3131/fm-vendedor-recuperacao-vendas`, branch `main`. Ele lê o
`render.yaml` e pede os valores marcados `sync: false`:

| Variável | Valor |
|---|---|
| `FM_DATABASE_URL` | URL **Internal**, trocando usuário e senha pelos de `fm_app` |
| `FM_DATABASE_ADMIN_URL` | URL **Internal** do usuário padrão (dono; só migrations e backup) |
| `FM_SECRETS_KEYS` | a chave do passo 2 |
| `FM_GOOGLE_CLIENT_ID` e `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | o Client ID do passo 3 |
| `FM_PUBLIC_BASE_URL` e `API_PROXY_TARGET` | endereço público da API (`https://fm-seller-api….onrender.com`) |
| `FM_WEB_ORIGIN` | endereço público do painel (`https://fm-seller-web….onrender.com`) |
| `FM_PLATFORM_CAKTO_SECRET`, `FM_PLATFORM_HOTMART_HOTTOK`, `FM_AI_API_KEY` | deixe vazio por enquanto |

Os endereços só são conhecidos depois da criação (o Render pode acrescentar um sufixo ao nome). Se
precisar, deixe em branco, crie, copie os endereços e preencha em *Environment* de cada serviço; depois
redeploy. O Render só pergunta os `sync: false` na criação: variável nova depois entra manualmente.
`FM_WHATSAPP_LIVE` fica `false` até a conta de teste da Meta existir.

Se o Render **recusar** o arquivo, ele diz a linha. Mande a mensagem que eu corrijo.

## Passo 5: conferir (cole aqui só os resultados, nunca segredos)

1. Deploy da API: no log deve aparecer `Aplicadas: 0001_core.sql, …, 0007_operacao.sql`.
2. `https://<api>/v1/health` → `{"status":"ok",…}` e `https://<api>/v1/ready` → `{"status":"ready"}`.
3. **`https://<painel>/v1/health` deve devolver o mesmo JSON.** Se der 404, o Render não passou
   `API_PROXY_TARGET` ao build do painel e o login não vai funcionar: me avise.
4. `https://<painel>/login` abre.
5. Criar o primeiro cliente, no Shell da API (Render → fm-seller-api → Shell):
   `python -m fm_seller.cli create-tenant --name "Minha Loja" --email <seu Gmail> --plan fase-1`
6. Com o Client ID real e o seu e-mail como usuário de teste, entrar pelo painel.
7. No Shell do worker ou do cron: `python -m fm_seller.cli ops-check` → `OK: nada a reportar.`
   (com o worker ligado há mais de 1 minuto).
8. Ligar a notificação de falha do cron `fm-seller-ops-check` a um e-mail.

## O que continua sem prova

Blueprint e nomes de plano (rascunho); repasse de variável de build do Render para o Docker; envio real,
templates e teste de conexão da Meta, Gemini, Cakto e Hotmart (dependem das contas, ver
`docs/LANCAMENTO_MVP.md`). Nada disso deve ser dado como funcionando antes do teste real.
