# fm-vendedor-recuperacao-vendas

SaaS de vendedor IA e recuperação de vendas por WhatsApp, Messenger e Instagram, com login Google,
cobrança e anúncios em fases. Produto comercial da F&M Tecnologia, **Web First** (uma única construção).

## Estado

Blocos 1 (fundação), 2 (eventos e recuperação), 3 (conversas e vendedor IA), 4 (oportunidades próprias) 5 (adaptador Gemini), 6 (limites e custo) e 7 (WhatsApp real e templates da Meta) concluídos, tudo contra simuladores ou servidor falso. Ver `docs/00_GATE_WEB_FIRST.md` para o que está pronto e o que falta.

| Peça | Estado |
|---|---|
| API `/v1` (FastAPI), login Google, sessão, planos, central de conexões | Feito e testado |
| Banco Postgres com isolamento por cliente (RLS), migrations, auditoria | Feito e testado |
| Credenciais de clientes cifradas (AES-256-GCM, com rotação de chave) | Feito e testado |
| Painel web (Next.js): login, visão geral, conexões | Feito; testado em celular, tablet e desktop |
| Integrações reais (Meta, Cakto, Hotmart, IA) | Código de WhatsApp e Gemini feito contra servidor falso; **nada validado com conta real** |
| Recebimento de eventos Cakto/Hotmart (segredo, dedupe, reprocesso) | Feito e testado com payloads **sintéticos**; formato real a confirmar |
| Motor de recuperação (carrinho, PIX, boleto, recusa), worker, opt-out, janela de silêncio, limite diário | Feito e testado; envio real pelo WhatsApp existe mas fica **desligado** (`FM_WHATSAPP_LIVE`); simulador em dev/teste |
| Compra do próprio SaaS cria cliente, plano e convite | Feito e testado com payloads sintéticos |
| Tela "Recuperação" (ajustes com consentimento, sequências, templates, bloqueios, casos) | Feito; testado em celular, tablet e desktop |
| Entrada do WhatsApp (assinatura, mensagens, status de entrega, "SAIR"), fila de saída, conversas | Feito e testado com payloads sintéticos no formato documentado pela Meta; **não testado com conta real** |
| Vendedor IA: ofertas do cliente, porta de modelo, limites (preço/link só do cadastro), transferência para pessoa | Feito e testado com **simulador**; adaptador do Gemini feito e testado contra servidor falso; **falta validar com a chave real (`ai-check`)** |
| Telas Conversas e Vendedor IA | Feito; testado em celular, tablet e desktop |
| Oportunidades próprias para comércio local: conversa que esfriou, registro avulso, planilha CSV, "vendido/perdi" | Feito e testado; sem integração externa (o dado já está no sistema) |
| Limite mensal de IA por plano (dado), medição de uso, limite diário de contatos novos por número | Feito e testado; **valores dos planos são provisórios** |
| WhatsApp Cloud API real: envio de template/texto, envio de templates para aprovação, sincronização de status, teste real da conexão | Feito e testado contra **servidor falso** (formato da documentação da Meta); **desligado por padrão; falta validar com conta real** |
| Messenger/Instagram, cobrança própria, anúncios | **Não feito.** Próximos blocos |

Contas e configurações externas ainda pendentes: ver `docs/PENDENCIAS_EXTERNAS.md`. Roteiro e critérios de lançamento: `docs/LANCAMENTO_MVP.md`. Encaixe futuro com o Core: `docs/adr/0002-encaixe-com-o-core-v2.md`.

## Estrutura

```
apps/api   API Python (FastAPI) + migrations SQL
apps/web   Painel Next.js (TypeScript)
docs/      Gate Web First, arquitetura, ADRs, operação
scripts/   Bootstrap do banco
```

## Rodar localmente

```bash
# 1. Banco (Postgres 16) e papéis
docker compose up -d db
psql postgresql://postgres:dev_admin_only@localhost:5432/postgres \
  -v owner_pw=dev_owner_only -v app_pw=dev_app_only -f scripts/db/bootstrap_roles.sql
psql postgresql://postgres:dev_admin_only@localhost:5432/postgres -c "CREATE DATABASE fm_dev OWNER fm_owner"

# 2. API
cd apps/api && python -m venv ../../.venv && . ../../.venv/bin/activate && pip install -e ".[dev]"
export FM_ENV=dev FM_SECRETS_KEYS="$(python -m fm_seller.cli gen-key)"
python -m fm_seller.cli migrate
python -m fm_seller.cli create-tenant --name "Minha Loja" --email voce@exemplo.com --plan fase-1
uvicorn fm_seller.api.app:create_app --factory --port 8000

# 3. Painel
cd apps/web && npm ci
NEXT_PUBLIC_API_URL=http://localhost:8000 NEXT_PUBLIC_DEV_LOGIN=1 npm run dev
```

Em dev o login simulado aceita o e-mail do convite criado acima. **Nunca** ligue `NEXT_PUBLIC_DEV_LOGIN`
em staging ou produção; a API também recusa o verificador simulado nesses ambientes.

## Qualidade

```bash
cd apps/api && ruff check . && ruff format --check . && mypy && pytest
cd apps/web && npm run typecheck && npm run lint && npm run build
cd apps/web && PLAYWRIGHT_CHROMIUM_PATH=... npx playwright test   # celular, tablet e desktop
```

Regras do repositório em `AGENTS.md`.
