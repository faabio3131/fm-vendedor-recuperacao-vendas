# fm-vendedor-recuperacao-vendas

SaaS de vendedor IA e recuperação de vendas por WhatsApp, Messenger e Instagram, com login Google,
cobrança e anúncios em fases. Produto comercial da F&M Tecnologia, **Web First** (uma única construção).

## Estado

Bloco 1 (fundação) concluído. Ver `docs/00_GATE_WEB_FIRST.md` para o que está pronto e o que falta.

| Peça | Estado |
|---|---|
| API `/v1` (FastAPI), login Google, sessão, planos, central de conexões | Feito e testado |
| Banco Postgres com isolamento por cliente (RLS), migrations, auditoria | Feito e testado |
| Credenciais de clientes cifradas (AES-256-GCM, com rotação de chave) | Feito e testado |
| Painel web (Next.js): login, visão geral, conexões | Feito; testado em celular, tablet e desktop |
| Integrações reais (Meta, Cakto, Hotmart, IA) | **Não feito.** Próximos blocos, primeiro contra simuladores |
| Recuperação de vendas, vendedor IA | **Não feito.** Bloco 2 em diante |

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
