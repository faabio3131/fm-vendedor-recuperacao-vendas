# ADR-0001 — Stack e isolamento por cliente

Status: PROPOSTA (aguarda confirmação do Diretor) · Data: 2026-10-03

## Contexto

Produto comercial multi-cliente, Web First, que guarda credenciais de terceiros (Meta, pagamento).
O vazamento entre clientes é o pior defeito possível.

## Decisão

- API em Python/FastAPI; painel em Next.js/TypeScript. Alinhado a Ruff, Mypy strict e Pytest usados
  nos outros produtos da F&M e ao Next.js do FM Control Center (ADR-005).
- Postgres com RLS **forçada**; a API conecta com papel sem bypass; migrations em SQL puro versionado.
- Migrations rodam com outro papel (dono), separado do papel do app.
- Hospedagem: Render (decisão do Diretor em 03/10/2026). `render.yaml` é rascunho não validado.

## Consequências

- Esquecer um `WHERE tenant_id` não vaza dados: o banco bloqueia. Há testes de isolamento em Postgres real.
- Duas linguagens no mesmo repositório; o contrato entre elas é a API `/v1`.
- Ambientes precisam criar os papéis `fm_owner` e `fm_app` (`scripts/db/bootstrap_roles.sql`).
