# Gate Web First — 12 perguntas

Política: `FM_TECHNOLOGY_WEB_FIRST_POLICY.md` (kordena-fiscal-engine-v2, 14/09/2026). Atualizado em 03/10/2026.

Legenda: **RESPONDIDO** (existe e foi testado) · **PARCIAL** · **PENDENTE** (decisão ou trabalho em aberto).

| # | Pergunta | Estado | Resposta e evidência |
|---|---|---|---|
| 1 | Superfície web principal | RESPONDIDO | Painel Next.js responsivo (`apps/web`): login, visão geral, recuperação, conexões. |
| 2 | Contrato HTTP/API | RESPONDIDO | API `/v1` com OpenAPI automático do FastAPI. Erros com código estável `{error:{code,message}}`. |
| 3 | Autenticação e sessão | PARCIAL | Login Google (ID token) + sessão opaca em cookie HttpOnly/SameSite, checagem de origem em métodos de escrita. Validador testado offline com chaves locais; **não testado contra o Google real** (falta o Client ID). |
| 4 | Autorização e multi-tenancy | RESPONDIDO | `tenant_id` + RLS forçada + papéis dono/admin/agente. Testes de isolamento em Postgres real, inclusive das tabelas do Bloco 2. |
| 5 | Banco de produção | RESPONDIDO | Postgres 16. |
| 6 | Migrations | RESPONDIDO | SQL versionado, checksum, uma transação por arquivo. Papel de migration separado do papel do app. |
| 7 | Segredos | PARCIAL | Credenciais de clientes cifradas (AES-256-GCM, rotação). Chaves da plataforma por variável de ambiente. Falta definir o cofre em produção e a re-cifragem em lote. |
| 8 | Deploy e rollback | PENDENTE | Dockerfiles e `render.yaml` rascunhados, **não validados no Render**. Rollback ainda não definido. |
| 9 | Logs, métricas e alertas | PARCIAL | Log JSON com `request_id`, sem corpo/cookies; worker registra um resumo por ciclo (recuperação, vendedor, fila de saída). Métricas e alertas (webhook parado, fila, falha de envio, nota do WhatsApp) ainda não existem. |
| 10 | Backup e restore | PENDENTE | Proposta: diário, 30 dias, com teste de restauração. Depende do plano do banco na hospedagem. |
| 11 | Ambientes dev/staging/prod | PARCIAL | Configuração por `FM_ENV`; verificador simulado e testador simulado só em dev/teste. Staging e produção ainda não existem. |
| 12 | Teste em celular, tablet e desktop | RESPONDIDO | Playwright nos 3 tamanhos (sem rolagem horizontal, segredo não vaza na tela). Passou localmente e no CI do GitHub (03/10/2026): jobs api, web e e2e verdes. |

## Pendências para fechar o gate

Confirmar hospedagem e criar staging (8, 11) · Client ID do Google (3) · definir cofre de chaves (7) ·
retenção de backup (10) · métricas e alertas (9).
