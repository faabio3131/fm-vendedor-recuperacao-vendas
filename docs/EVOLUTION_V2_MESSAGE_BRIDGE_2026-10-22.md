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

## Gate de contrato de versão exata — 22/10/2026

**Servidor efetivo:** Contabo `vmi3683268`, container `evolution_api`, `evoapicloud/evolution-api:latest`, API reportou **2.3.7** via GET `/`. Imagem Docker `sha256:966625532d9076a2381e973a271307d107e6f070450de3abeeea8bd18be07252`, criada em 06/05/2026. Tag `latest` não é âncora imutável; proteger o próximo deploy fixando digest em configuração aprovada.

**Provas observadas somente leitura:** GET `/instance/fetchInstances` autenticado: HTTP 200, uma instância, reportada `connecting`. GET `/instance/connectionState/{instância}`: HTTP 200, estado `close` (verificação mais específica). GET `/webhook/find/{instância}`: HTTP 200; webhook ainda não configurado no formato esperado. GET `/event/webhook/find/{instância}`: 404, rota não suportada nesta instalação. Nenhum callback, mensagem ou alteração na Evolution foi executado neste gate.

**Contrato confrontado com código-fonte da tag oficial `2.3.7`, commit `cd800f2976e1e5b682fbf86a01ee4d85ae61f370`:**
- `WebhookRouter` usa `POST /webhook/set/{instance}` e `GET /webhook/find/{instance}`;
- `webhookSchema` exige **`{"webhook":{"enabled":true,"url":"https://...","byEvents":false,"base64":false,"events":["MESSAGES_UPSERT","MESSAGES_UPDATE"],"headers":{"x-fm-evolution-auth":"..."}}}`**; os campos no nível raiz da versão anterior da PR eram inválidos. Corrigidos.
- `WebhookController` persiste `headers` no registro da instância e os inclui no envio; a PR passou a realizar readback do webhook e recusa divergências inclusive da chave, sem expô-la ao browser.
- `POST /message/sendText/{instance}` usa `{"number":"<numeros>","text":"<texto>"}` conforme `textMessageSchema`. Nenhuma mensagem real foi enviada.
- O callback contém `event`, `instance`, `data`; versão validada por código-fonte e testes sintéticos. Eventos `@lid` sem telefone verificável permanecem ignorados (fail-closed); devem ser tratados em jornada piloto com evidência antes de alegar 100% do universo de contatos.

**Limites deste gate:** verificação de rota, schema e testes com mocks não comprovam persistência em produção sob restart, autorização de envio ou chegada de mensagem. As flags `FM_EVOLUTION_ENABLED` e `FM_EVOLUTION_SEND_ENABLED` continuam desligadas na versão corrente implantada. Não houve deploy, merge, novo container nem alteração de instância ou webhook real.

**Critério próximo:** CI completa verde, revisão independente, merge/deploy explicitamente autorizados; após implantado, registrar webhook na instância correspondente ao tenant, comprovar `/webhook/find` real com headers sem vazamento, escanear QR com número do piloto, comprovar `open`, mensagens inbound/IA/outbound e replay, monitorar restart e executar rollback se necessário.

## Reconciliação do Compose da Contabo — 22/10/2026

O gate da v2.3.7 identificou que o Compose canônico não repassava as flags de Evolution ao backend. A PR agora conecta `FM_EVOLUTION_ENABLED`, `FM_EVOLUTION_SEND_ENABLED`, `FM_EVOLUTION_API_BASE_URL`, `FM_EVOLUTION_API_KEY` e `FM_EVOLUTION_WEBHOOK_KEY` ao único ambiente compartilhado por API e Worker. QR e envio continuam **false** por padrão, sem credenciais embutidas no Git e sem usar outro serviço de negócio.

**Evidência:** código da release Evolution `2.3.7` exige payload aninhado `{"webhook":{"enabled":true,"url":"...","headers":{...},"byEvents":false,"base64":false,"events":[...]}}`; o adaptador foi corrigido e passou a conferir com `GET /webhook/find/{instância}` se URL, eventos e cabeçalho secreto ficaram efetivamente salvos. O endpoint `POST /message/sendText/{instância}` aceita `number` e `text`. Testes locais de contrato passaram. Imagem instalada: `sha256:966625532d9076a2381e973a271307d107e6f070450de3abeeea8bd18be07252`; não atualizar `latest` implicitamente.

**Bloqueios para GO real:** apesar do contrato corrigido e das verificações internas, a instância existente na Contabo retornou `close` no `connectionState` e ainda não existe prova de pareamento, registro de webhook com autenticação, recebimento/envio real nem QR lido pelo operador. Não confundir 4/4 CI com homologação real. Antes de deploy: verificar escopo de produção, preparar rollback, obter aprovação específica e configurar credenciais reais sob gestão segura; não reprocessar pagamentos nem tocar no banco migrado.
