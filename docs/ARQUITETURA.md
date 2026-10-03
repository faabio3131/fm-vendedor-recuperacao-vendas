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

## Portas (para os próximos blocos)

`GoogleVerifier`, `ConnectionTester` e, a seguir, canais de mensagem, IA, checkout e pagamento.
Cada porta tem versão simulada para dev/teste e adaptador real separado.
