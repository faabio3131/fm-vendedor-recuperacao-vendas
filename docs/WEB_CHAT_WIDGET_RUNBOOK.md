# Runbook — Chat IA embutido nos sites (widget `fm-chat-widget.js`)

> **Por que este documento existe:** este procedimento envolveu mudanças em 3 repositórios
> de site, no backend do AtendeVendeIA e em infraestrutura manual de servidor (nginx). Como o
> Claude não mantém memória entre sessões, qualquer continuidade do trabalho (novo site, nova
> oferta, debug de "a IA não responde") depende deste runbook estar completo e atualizado.
> Escrito em 2026-11-15, depois de ativar o chat em Iron Fit Core, NFCore, Kordena e FM
> Tecnologia (institucional).

## 1. Visão geral da arquitetura

```
Visitante no site (ex.: ironfitcore.com.br)
  └─ assets/fm-chat-widget.js  (balão de chat flutuante, mesmo arquivo nos 4 sites)
       └─ POST https://app.atendevendeia.com.br/web/chat
            { site: "ironfit", session_id: "<uuid no localStorage do navegador>", message: "..." }
            │
            ▼
       Nginx Proxy Manager (container fc004b7d17ba_nginx_proxy_manager)
       config do host app.atendevendeia.com.br, location /web/chat
            │  proxy_pass → fm-atendevendeia-api-1:8000/v1/web/chat
            ▼
       API do AtendeVendeIA (apps/api/src/fm_seller/api/routes/web_chat.py)
            │  resolve "site" → tenant_id via env var FM_WEB_CHAT_TENANTS
            │  ingest_web_message → handle_conversation_now → _handle_conversation
            │  IA responde usando apenas offers + knowledge_items daquele tenant
            ▼
       Postgres (container fm-atendevendeia-db-1, banco fm_atendevendeia)
            tenant_settings / offers / knowledge_items / conversations
```

Cada site tem seu próprio `site` slug (ex.: `ironfit`, `nfcore`, `kordena`, `fm`), que mapeia
para um `tenant_id` fixo no backend. **O botão flutuante de WhatsApp foi substituído pelo
chat** nos 4 sites — o WhatsApp continua existindo como fallback dentro do próprio widget
(quando a IA pede handoff, ou quando `apiBase` está vazio/indisponível).

## 2. Onde está cada peça

| Peça | Repositório / local | Observação |
|---|---|---|
| Widget JS (balão + painel) | `assets/fm-chat-widget.js` em cada site (`site-ironfit-core`, `nfcore-site`, `kordena-site`, `fm-site-novo`) | Arquivo **idêntico** nos 4 repos. Lê config de `window.FM_CHAT_CONFIG`. |
| Config por site | Inline `<script>` antes do widget, em cada página HTML (ou no `page()` de `src/build.mjs` no caso do fm-site-novo) | Define `site`, `accentColor`, `greeting`, `apiBase`, `whatsappNumber`, `whatsappPrefill`. |
| Backend da IA | `fm-vendedor-recuperacao-vendas` (este repo), `apps/api/src/fm_seller/` | `channels/web.py` (ingestão), `ai/seller.py` (`_handle_conversation`, decide responder ou fazer handoff), `api/routes/web_chat.py` (rota HTTP). |
| Mapeamento site→tenant | Variável de ambiente `FM_WEB_CHAT_TENANTS` no container `fm-atendevendeia-api-1` | Formato `slug1:uuid1,slug2:uuid2,...`. Ver seção 5. |
| CORS do widget | Variável de ambiente `FM_WEB_CHAT_ORIGINS` | Lista de origens (domínios dos sites) permitidas. |
| Dados que a IA usa | Tabelas `offers`, `knowledge_items`, `tenant_settings` no Postgres | A IA **nunca inventa** preço ou link — só usa o que está cadastrado aqui. |
| Rota pública `/web/chat` | Config manual do Nginx Proxy Manager, host `app.atendevendeia.com.br` | **Não é gerenciada pela UI do NPM** — ver seção 6, é o ponto mais frágil do procedimento. |

## 3. IDs de tenant (referência rápida, 2026-11-15)

| Site / produto | `site` slug no widget | `tenant_id` |
|---|---|---|
| Iron Fit Core | `ironfit` | `12ca8773-1295-4dd3-b5a0-e97782b4b07c` |
| NFCore | `nfcore` | `7b3eab48-06d9-43d0-9c3a-96066e88aeb3` |
| Kordena | `kordena` | `00055b8a-e793-4cb7-87dc-72d72fa11d8d` |
| FM Tecnologia (institucional) | `fm` | `7b2161ff-f37a-4408-bc32-07bba27e5b81` |

Esses 4 slugs e IDs já estão configurados em `FM_WEB_CHAT_TENANTS` e `FM_WEB_CHAT_ORIGINS` no
servidor (checado em 2026-11-15) — **não precisa reconfigurar nada no backend para esses 4
sites**, só manter os dados (ofertas/conhecimento) atualizados.

## 4. Como adicionar o chat a um site NOVO (produto futuro)

Passo a passo completo, do zero:

### 4.1 Backend — cadastrar o tenant (se ainda não existir)
Se o produto novo já tem uma linha em `tenants`, pule esta etapa. Senão, criar o tenant
primeiro (fora do escopo deste runbook — ver `ARQUITETURA.md`).

### 4.2 Backend — habilitar a IA para esse tenant
```sql
insert into tenant_settings (tenant_id, ai_enabled, ai_persona, recovery_enabled, cold_enabled)
values ('<tenant_id>', true, '<texto descrevendo o tom e o escopo do atendente>', false, false)
on conflict (tenant_id) do update set ai_enabled = true, ai_persona = excluded.ai_persona;
```
**Isso é obrigatório.** Sem essa linha (ou com `ai_enabled=false`), toda conversa cai em
handoff com `handoff_reason = 'ia_desligada'` e o visitante recebe só a mensagem genérica de
fallback — foi exatamente o que aconteceu na primeira tentativa com o Iron Fit.

### 4.3 Backend — cadastrar ofertas reais
```sql
insert into offers (id, tenant_id, name, description, price_cents, currency, payment_url, active, brand, category)
values (gen_random_uuid(), '<tenant_id>', '<nome do plano>', '<descrição>', <preço em centavos>, 'BRL', '<link real de checkout ou contato>', true, '<marca>', '<categoria>');
```
Regras:
- **Nunca inventar preço ou link.** Usar sempre a fonte mais atual e oficial (o próprio site
  do produto, página de preços, ou `fm-site-novo/src/products.mjs` quando for um produto FM).
- Se o produto ainda não tem checkout self-service (caso do NFCore e da Kordena em
  2026-11-15), o `payment_url` pode ser um link de WhatsApp (`wa.me/...?text=...`) com a
  mensagem de interesse — copiar o texto exato já publicado no site, não reescrever.
- Sem nenhuma linha em `offers`, toda conversa cai em handoff com
  `handoff_reason = 'sem_ofertas'` antes mesmo de chamar o modelo de IA.

### 4.4 Backend — cadastrar conhecimento (perguntas e respostas)
```sql
insert into knowledge_items (id, tenant_id, question, answer, active)
values (gen_random_uuid(), '<tenant_id>', '<pergunta frequente>', '<resposta>', true);
```
5 a 8 itens cobrindo: o que é o produto, preço, teste grátis/trial, principais dúvidas
específicas do produto (ex.: cobertura municipal no caso do NFCore), e como falar com a
equipe. Usar sempre texto já publicado no site/FAQ como fonte, nunca inventar.

### 4.5 Backend — registrar o `site` slug
Editar a env var `FM_WEB_CHAT_TENANTS` do container `fm-atendevendeia-api-1` (ver seção 7 —
como editar env vars em produção) adicionando `,<novo_slug>:<tenant_id>` ao final da lista
existente. Adicionar também o domínio do novo site em `FM_WEB_CHAT_ORIGINS`. Depois de
editar, reiniciar o container da API para a env var ser lida de novo.

### 4.6 Site — adicionar o widget
1. Copiar `assets/fm-chat-widget.js` de qualquer um dos 4 sites já configurados (o arquivo é
   genérico, não precisa adaptar nada nele).
2. No HTML de cada página (ou no template/`build.mjs` se o site for gerado), substituir o
   antigo botão flutuante de WhatsApp:
   ```html
   <a class="fab" href="https://wa.me/...">...</a>
   ```
   por:
   ```html
   <script>window.FM_CHAT_CONFIG={
     site:"<slug cadastrado no passo 4.5>",
     productName:"<nome do produto>",
     accentColor:"<cor da marca em hex>",
     avatarInitial:"<letra do avatar>",
     greeting:"<mensagem de saudação do chat>",
     apiBase:"https://app.atendevendeia.com.br/web",
     whatsappNumber:"5511978350851",
     whatsappPrefill:"<mensagem padrão do fallback de WhatsApp>"
   };</script>
   <script src="assets/fm-chat-widget.js" defer></script>
   ```
   **Atenção:** `apiBase` deve terminar em `/web` (sem `/chat`) — o widget já concatena
   `apiBase + "/chat"` internamente.

### 4.7 Testar antes de publicar
```bash
curl -s -X POST https://app.atendevendeia.com.br/web/chat \
  -H 'Content-Type: application/json' \
  -H 'Origin: https://<dominio-do-site>' \
  -d '{"site":"<slug>","session_id":"test-001","message":"<pergunta de teste>"}'
```
Resposta esperada: `{"reply":"<resposta grounded nas ofertas/conhecimento>","handoff":false}`.
Se vier a mensagem genérica de fallback ou `handoff:true`, ver seção 8 (diagnóstico).

### 4.8 Publicar
Comitar e dar push no repositório do site (GitHub Pages publica automaticamente a partir da
`main`, exceto `fm-site-novo`, que precisa rodar `npm run build` antes para gerar `docs/` a
partir de `src/`).

## 5. A env var `FM_WEB_CHAT_TENANTS` (mapeamento site → tenant)

Formato: `slug1:uuid-do-tenant-1,slug2:uuid-do-tenant-2,...` — uma única string, sem espaços.
Em 2026-11-15, o valor em produção é:
```
fm:7b2161ff-f37a-4408-bc32-07bba27e5b81,nfcore:7b3eab48-06d9-43d0-9c3a-96066e88aeb3,ironfit:12ca8773-1295-4dd3-b5a0-e97782b4b07c,kordena:00055b8a-e793-4cb7-87dc-72d72fa11d8d
```
Se o `site` enviado pelo widget não estiver nessa lista, a API responde 404 ("Site não
reconhecido") e o widget cai no fallback de WhatsApp.

A env var `FM_WEB_CHAT_ORIGINS` (CORS, separado do CORS geral da API) em 2026-11-15:
```
https://fmtecnologiaia.com.br,https://nfcore.com.br,https://ironfitcore.com.br,https://kordenaia.com.br
```

## 6. A rota nginx `/web/chat` — ponto frágil do procedimento

A API do AtendeVendeIA (porta 8000) originalmente só era acessível internamente (painel
Next.js em `app.atendevendeia.com.br` → `fm-atendevendeia-web-1:3000`). Não havia subdomínio
público nem DNS wildcard disponível para expor a API diretamente. A solução adotada foi
**reaproveitar o domínio já existente e válido** `app.atendevendeia.com.br`, adicionando uma
`location` nova no mesmo host:

Arquivo (no servidor, dentro do container do Nginx Proxy Manager):
`/home/SaaS/infra/nginx/data/nginx/proxy_host/2.conf`

Bloco adicionado (acima do `location / { ... }` existente, que continua intacto):
```nginx
  # --- Custom addition: FM web chat widget bridge (MVP) ---
  # Added manually (not managed by the Nginx Proxy Manager UI/DB).
  # Routes the Iron Fit / FM chat widget to the AtendeVendeIA AI seller backend.
  location /web/chat {
    proxy_pass http://fm-atendevendeia-api-1:8000/v1/web/chat;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
  }
  # --- end custom addition ---
```
Aplicado com `nginx -t` (valida sintaxe) seguido de `nginx -s reload`, dentro do container
`fc004b7d17ba_nginx_proxy_manager`. Backup salvo como `2.conf.bak-20261115` no mesmo diretório.

Pré-requisito de rede: o container da API (`fm-atendevendeia-api-1`) precisou ser conectado
à mesma rede Docker do NPM, com:
```bash
docker network connect saas-network fm-atendevendeia-api-1
```
(operação aditiva, não tira a API de suas redes originais `fm-atendevendeia_app_only` /
`fm-atendevendeia_db_only`.)

### ⚠️ Risco conhecido: a UI/API do Nginx Proxy Manager pode sobrescrever esse arquivo
Este arquivo de config normalmente é **gerado automaticamente pelo NPM** a partir do banco de
dados dele, sempre que alguém edita aquele mesmo proxy host (`app.atendevendeia.com.br`) pela
interface do NPM. Se isso acontecer, a edição manual da `location /web/chat` é perdida
silenciosamente, e o chat para de funcionar em todos os sites (erro genérico de rede no
widget, que cai no fallback de WhatsApp).

**Como detectar:** `curl -s -o /dev/null -w "%{http_code}" https://app.atendevendeia.com.br/web/chat -X POST -d '{}'` deixa de retornar `422` (erro de validação esperado) e passa a
retornar `404` do painel Next.js.

**Como corrigir:** reaplicar o bloco da seção 6 no mesmo arquivo `2.conf` (ou no arquivo atual
do proxy host, caso o número tenha mudado) e repetir `nginx -t && nginx -s reload`.

## 7. Como editar env vars da API em produção

```bash
ssh root@<ip-do-servidor>
cd /home/SaaS/fm-atendevendeia   # ou caminho equivalente do docker-compose da API
# editar o arquivo .env usado pelo serviço fm-atendevendeia-api-1 (adicionar o novo slug)
docker compose restart api       # ou: docker restart fm-atendevendeia-api-1
docker exec fm-atendevendeia-api-1 printenv | grep WEB_CHAT   # confirmar o novo valor
```
(O nome exato do serviço/arquivo compose pode variar — usar `docker ps` para confirmar o
container certo antes de editar.)

## 8. Diagnóstico — por que a IA não respondeu de verdade

A tabela `conversations.handoff_reason` é o sinal mais rápido para saber o que aconteceu.
Em ordem de verificação em `_handle_conversation` (`apps/api/src/fm_seller/ai/seller.py`):

| `handoff_reason` | Causa | Correção |
|---|---|---|
| `ia_desligada` | Falta linha em `tenant_settings`, ou `ai_enabled=false` | Seção 4.2 |
| `sem_ofertas` | Zero linhas ativas em `offers` para o tenant | Seção 4.3 |
| `assinatura_inativa` | `tenant_plans.status` bloqueia o serviço | Verificar plano/assinatura do tenant |
| `trial_limit_or_expired` | Trial expirado/bloqueado | Verificar status do trial |
| `pediu_atendente` | O próprio visitante pediu para falar com humano | Comportamento esperado, não é erro |
| `limite_de_respostas` | Teto de respostas por hora atingido | Esperado sob volume alto; ajustar limite se necessário |

Se `handoff_reason` estiver vazio/nulo e ainda assim vier a mensagem genérica
("Desculpe, não consegui responder agora..."), o erro está em `latest_reply` retornando
`None` — nesse caso, checar logs do container da API (`docker logs fm-atendevendeia-api-1`).

Outros pontos de falha já encontrados neste procedimento:
- **Rota errada no widget:** o JS sempre faz `fetch(apiBase + "/chat")` — `apiBase` tem que
  terminar em `/web` para o resultado ser `.../web/chat`, não `.../web-chat` nem outra coisa.
- **CORS:** se o domínio do site não estiver em `FM_WEB_CHAT_ORIGINS`, o navegador bloqueia a
  chamada antes mesmo de chegar na API (erro de CORS no console do navegador, não no widget).
- **Nginx sobrescrito:** ver seção 6.

## 9. Testando um site já configurado (smoke test rápido)

```bash
curl -s -X POST https://app.atendevendeia.com.br/web/chat \
  -H 'Content-Type: application/json' \
  -H 'Origin: https://ironfitcore.com.br' \
  -d '{"site":"ironfit","session_id":"smoke-test","message":"Quanto custa o plano mais barato?"}'
```
Trocar `Origin`, `site` e a pergunta para testar `nfcore`, `kordena` ou `fm`. Resposta
esperada sempre com `"handoff":false` e um preço/informação real, nunca genérica.

## 10. Histórico de mudanças relevantes (commits)

| Data | Repo | Commit | O que mudou |
|---|---|---|---|
| 2026-11-15 | `site-ironfit-core` | `5ebcf56` | Primeira ativação do widget, substitui botão de WhatsApp |
| 2026-11-15 | `nfcore-site` | `e876efa` | Widget wiring (cor `#9E11D6`) |
| 2026-11-15 | `kordena-site` | `40aa2e3` | Widget wiring (cor `#4F7BF2`) |
| 2026-11-15 | `fm-site-novo` | `7c98a9c` | Widget wiring no `src/build.mjs` (institucional, cor `#128C7E`), preservando o checkout Cakto do Iron Fit já aprovado |
| 2026-11-15 | infra (servidor) | — | `docker network connect saas-network fm-atendevendeia-api-1`; edição manual de `2.conf` (seção 6) |
| 2026-11-15 | banco de dados | — | Seeds de `tenant_settings`/`offers`/`knowledge_items` para `ironfit`, `nfcore`, `kordena`, `fm` |

---
*Este documento deve ser atualizado sempre que: um novo site/produto ganhar o chat, a rota
nginx precisar ser reaplicada, ou os IDs/slugs de tenant mudarem.*
