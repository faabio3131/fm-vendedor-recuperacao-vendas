# AtendeVendeIA — Bridge Evolution v2 de mensagens (22/10/2026)

**Escopo:** mesma aplicação FastAPI, mesma persistência PostgreSQL/RLS, mesmo motor de conversas/IA, mesma fila de saída, mesmo worker. Complementa a PR #73 já reconciliada à `main` no HEAD anterior `25ef633`. Esta entrega é candidata; não constitui deploy nem homologação externa.

## Caminho implementado

1. Pareamento QR existente passa a configurar webhook da **instância** em `POST /webhook/set/{instance}`, sem webhook global. O endpoint público é a própria aplicação: `/v1/webhooks/evolution/{instance}`.
2. Callback exige `x-fm-evolution-auth`, derivado de chave de plataforma independente por HMAC SHA-256 com tenant e nome de instância. O browser nunca recebe a chave, e dados no corpo (`instance`, `data.key`) **não são autoridade**.
3. A conexão é carregada pelo `public_id` e pelo provider `evolution`, com descriptografia na autoridade do backend, validação de tenant ativo e comparação exata da instância derivada do tenant.
4. Apenas `messages.upsert` de contato individual (`@s.whatsapp.net`), `fromMe=false`, ID válido e texto suportado entram pelo `record_inbound` canônico (dedupe/opt-out). Grupos, LID e mídia não suportada não disparam IA automaticamente.
5. Worker existente chama `run_ai_replies`, que usa IA e enfileira respostas na `messages` canônica; `flush_outbox` escolhe **uma única** conexão WhatsApp conectada por tenant. Duas simultâneas são erro fail-closed (`canal_ambiguo`), não fallback.
6. Adaptador `EvolutionSender` consulta sessão remota (state=open) antes de `POST /message/sendText/{instance}`. Envio usa `FM_EVOLUTION_SEND_ENABLED` separado de `FM_EVOLUTION_ENABLED`. Estado incerto de HTTP e resposta sem ID não dispara retry; o outbox registra falha/resultado incerto como antes.
7. Motor de recuperação preserva regras de consentimento, políticas de quiet hours, caps, opt-out e idempotência. Para Evolution usa o corpo da configuração do template local sem exigir aprovação de template da Meta; essa aprovação continua obrigatória quando a conexão selecionada é da Meta.
8. O webhook `messages.update` atualiza status monotonicamente por tenant e provider_message_id, sem aceitar evento de alteração de sessão como prova de `connected`.

## Flags obrigatórias (desligadas por padrão)

- `FM_EVOLUTION_ENABLED=false`: QR/webhook não autorizados até configuração da versão instalada.
- `FM_EVOLUTION_API_BASE_URL`: HTTPS:443, sem path, credenciais, fragmento ou query.
- `FM_EVOLUTION_API_KEY`: chave da instância Evolution na infraestrutura FM, fora do git.
- `FM_EVOLUTION_WEBHOOK_KEY`: segredo **novo e independente**, no mínimo 32 caracteres, fora do git. Necessário para ativar QR+webhook.
- `FM_EVOLUTION_SEND_ENABLED=false`: envio real separado, somente após prova de webhook, pairing e identidade na versão exata Evolution v2.
- `FM_WHATSAPP_LIVE`: continua a controlar apenas o caminho Meta, preservado.

## Evidência necessária antes do deploy/GO

- GitHub API, lint, mypy strict, pytest com PostgreSQL/RLS; Web lint/typecheck/build/E2E; validação do router HTTP e casos de tenant crossing.
- Fixar versão/digest da Evolution, confirmar `webhook/set` com cabeçalho customizado e `message/sendText`, contratos de `messages.upsert` e `messages.update` nessa versão.
- Testar dois tenants, webhook sem cabeçalho, segredo incorreto, spoof de `instance`, `fromMe`, grupos, replay, sessão caída, timeout, 5xx, status stale e nenhuma duplicação.
- Só depois: pareamento real pelo navegador e teste com celular controlado; execução real de ida e volta; operação/observabilidade e rollback.
- Merge e deploy NÃO autorizados automaticamente por esta documentação. Não repetir migração Neon/Contabo já certificada, nem recriar Evolution/containers.

## Observação de segurança

Cabeçalhos customizados por webhook são documentados para Evolution v2, mas a imagem `evoapicloud/evolution-api:latest` deve ser reconciliada com sua versão exata antes de ativar. O sistema recusa webhooks sem prova do token, mesmo se o provedor antigo omitir o cabeçalho. Eventos repetidos são idempotentes, não tratados como novas mensagens.
