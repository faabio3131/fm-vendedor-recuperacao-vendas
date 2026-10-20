# AtendeVendeIA — liberação comercial governada na VPS Contabo (NPM existente)

**Estado:** IMPLEMENTAÇÃO DO PACOTE DE DEPLOY, ainda **NÃO IMPLANTADO**.  
**Baseline:** `main` `f8040738e3f6030c364fb8fabba311d242de86ed`; conferir CURRENT novamente na execução.  
**Não alterar a Evolution API existente, Nginx Proxy Manager, Cloudflare ou dados persistentes sem inventário e backup.**

## CURRENT observado (20/10/2026)

- Site institucional público: `https://atendevendeia.com.br/` respondeu **HTTP 200**, exibindo "Acesso em breve".
- O NPM mostrou `api.atendevendeia.com.br` encaminhado para `evolution_api:8080`, certificado Let's Encrypt e status Online.
- Teste HTTP externo de `https://api.atendevendeia.com.br/`, `/health` e `/health/live` respondeu **502**. Badge "Online" do NPM não prova backend Evolution operacional.
- A Contabo Cloud VPS 4 foi contratada, com NPM acessível pela porta **81 via HTTP**, risco a mitigar.
- Tentativa SSH por chave, não interativa, desde o computador autorizado para usuário `root` na VPS foi recusada. **Nenhum comando do Compose foi executado na VPS**.
- Repositório canônico `faabio3131/fm-vendedor-recuperacao-vendas` tem API, Next.js, Worker, Postgres com RLS, CI principal #162 success. A última auditoria de dependências #4 reportou falhas em **Python** e **Node**: diagnosticar e corrigir o relatório antes de cliente real.
- `AGENTS.md` atual **exige Meta Cloud API oficial e veta QR Code**. A existência de Evolution API no VPS não a transforma em adaptador certificado do SaaS. **Não ligar FM_WHATSAPP_LIVE=true**, nem informar a clientes que o WhatsApp está conectado até decidir o contrato e comprovar teste real.

## Arquitetura de implantação (TARGET)

`deploy/contabo/compose.yml` reutiliza os Dockerfiles originais. Componentes:
- `db`: PostgreSQL **exclusivo** do AtendeVendeIA, role de owner `fm_owner` e role de app `fm_app` com RLS, volume dedicado e **sem porta pública**, apenas rede `db_only`.
- `bootstrap` (perfil `init`): migração e eventual tenant inicial **somente com comando governado explícito**, nunca automaticamente ao fazer `up`.
- `api` e `worker`: serviços separados, acesso apenas a DB e rede privada `app_only`, limites de recursos e logs.
- `web`: Next.js original, reescrita same-origin `/v1/*` para `http://api:8000/v1/*`, acesso ao NPM via **rede externa já existente** `NPM_EDGE_NETWORK`. Nome de destino NPM: `atendevendeia_web:3000`.
- `api.atendevendeia.com.br` atualmente pertence ao proxy da Evolution: **não sobrescrever sem realocação controlada**. O SaaS utilizará `https://app.atendevendeia.com.br` como portal e entrada de API same-origin, sujeito a DNS e certificado.
- `FM_WHATSAPP_LIVE` permanece **false** no Compose até aprovação e certificação independente do canal Meta ou novo ADR Evolution.

## Gate 0 — acesso e inventário somente leitura

A partir de console seguro da Contabo, confirmar SO, SSH por chave do operador, backup de acesso e usuário limitado. **Não enviar senha root, chave privada ou token pelo chat.** Antes de instalar:
```bash
id; uname -a; cat /etc/os-release
free -h; df -h; docker --version; docker compose version
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker network ls; docker volume ls
sudo ss -lntp
```
Identificar NPM/Evolution por `docker inspect`, evitando imprimir `Env` ou `Config` que possam conter senhas:
```bash
docker inspect <npm-container-name> --format '{{json .NetworkSettings.Networks}}'
docker inspect <evolution-container-name> --format '{{json .NetworkSettings.Networks}}'
```
Se faltar Docker Compose, resolver no sistema operacional depois de inventário e sem executar o antigo `deploy/hetzner/scripts/setup-server.sh`, pois ele **alteraria firewall, SSH, cron e usuário de modo conflitante** com o NPM instalado.

## Gate 1 — segurança e recuperação

1. Salvar/exportar configuração do NPM, certificados, banco e volumes da Evolution; fazer backup **fora do VPS** e testar restore.
2. Proteger NPM admin :81 via VPN/rede de administração/túnel SSH ou allowlist + TLS. Jamais mexer em SSH/firewall antes de verificar acesso de recuperação pela Contabo.
3. Confirmar hostnames, registros Cloudflare e acesso TLS do NPM; restringir portas públicas a 80/443 e SSH sob política autorizada.
4. Resolver a **HTTP 502** do endpoint da Evolution pela investigação de NPM network/target/health; não restaurar ou recriar container às cegas.
5. Verificar os avisos de auditoria de dependências GitHub #4 e aplicar correções na cadeia real.

## Gate 2 — deploy isolado da aplicação, sem liberar tráfego

Os passos abaixo são **referência de execução**, não execução comprovada:
```bash
# Verificar SHA/main, não instalar diretamente branch não certificada.
install -d -m 700 /home/SaaS/atendevendeia
cd /home/SaaS/atendevendeia
git clone https://github.com/faabio3131/fm-vendedor-recuperacao-vendas.git repo
cd repo
git rev-parse HEAD
cp deploy/contabo/.env.example /home/SaaS/atendevendeia/.env
chmod 600 /home/SaaS/atendevendeia/.env
# Preencher credenciais de forma segura FORA de git/histórico/shell.
# Descobrir rede do NPM EXISTENTE e definir NPM_EDGE_NETWORK.
docker compose --env-file /home/SaaS/atendevendeia/.env -f deploy/contabo/compose.yml config --quiet
docker compose --env-file /home/SaaS/atendevendeia/.env -f deploy/contabo/compose.yml up -d db
# Migrações e primeiro tenant controlados, somente após backup:
docker compose --env-file /home/SaaS/atendevendeia/.env -f deploy/contabo/compose.yml --profile init run --rm bootstrap
# Pré-checagem com acesso ao DB e papéis RLS antes de liberar servidor:
docker compose --env-file /home/SaaS/atendevendeia/.env -f deploy/contabo/compose.yml run --rm api python -m fm_seller.cli preflight
# Iniciar serviços sem frontend público mapeado no NPM:
docker compose --env-file /home/SaaS/atendevendeia/.env -f deploy/contabo/compose.yml up -d --build api worker web
docker compose --env-file /home/SaaS/atendevendeia/.env -f deploy/contabo/compose.yml ps
```
**Importante:** não executar bootstrap de tenant real sem dados e aprovação, nem aceitar preflight crítico. Build e operações podem exigir ajustes após gate de sintaxe e CI.

## Gate 3 — Web, autenticação e domínio

Somente depois de API + painel saudáveis na rede:
- Criar Proxy Host **`app.atendevendeia.com.br`** → `http://atendevendeia_web:3000` na rede `NPM_EDGE_NETWORK`. Usar TLS e HTTPS forçado; não duplicar Caddy.
- `/v1/health` via Next rewrite deve responder OK. Verificar também HTTP headers, CSP, cookies `Secure`, login Google com domínio autorizado, tenant isolation/RLS, rate limiting real atrás de NPM.
- Compreensivamente testar cadastro, convite, onboarding, upload/import, conversa, IA real, worker, quota, renovação/revogação de licença, webhook de pagamento real com assinatura, conciliação e rollback.
- Sem credenciais/provas reais de Meta/Evolution, manter WhatsApp live desligado; documentar como **pendência de liberação**, não "plataforma vendendo automaticamente".

## Gate 4 — comercialização oficial

Só substituir "Acesso em breve" no site público após: CI + security audit green, login e onboarding real, licença/billing completo, domínio TLS e backup/restore, observabilidade, testes de serviço de IA, WhatsApp real certificado (ou escopo contratual explicitamente reduzido), aceite jurídico de termos/LGPD e **Go/No-Go humano**.
Preservar ambiente Render até operação homologada. VPS única tem risco de falha total. O NPM só mostra disponibilidade da ponte, não disponibilidade do SaaS.

## Pendências que exigem decisão humana

- Como resolver incompatibilidade entre **Evolution API** instalada e autoridade atual do produto que estabelece **Meta Cloud API oficial**? Precisará de ADR e testes para uma eventual integração, mantendo identidade e consentimento, antes de colocar envios automáticos em produção.
- Qual o procedimento autorizado para instalar chave SSH pública no servidor? Atualmente não há sessão administrativa autenticada pelo executor.
- Confirmar registro DNS `app.atendevendeia.com.br` e provedor de cobrança, identidade Google, credenciais de IA, termos e política comercial, sem colocar segredos no chat.
