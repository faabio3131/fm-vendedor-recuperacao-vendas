# ADR-0006 — Evolution API como provedor adicional de WhatsApp no AtendeVendeIA

**Status:** DECISÃO ARQUITETURAL APROVADA PELO DIRETOR em 20/10/2026. **Implementação, CI, integração real e produção: PENDENTES.**  
**Identidade:** AtendeVendeIA (SaaS já construído), repositório canônico `faabio3131/fm-vendedor-recuperacao-vendas`. **Não confundir** com o produto VendeAtendeIA, ainda em construção, que possui fluxo de evolução próprio.  
**Baseline auditada:** `main` `f8040738e3f6030c364fb8fabba311d242de86ed`.

## Contexto

A regra 7 do `AGENTS.md` proibia qualquer canal WhatsApp fora da Meta e, portanto, impedia adaptar o AtendeVendeIA à Evolution API instalada na Contabo. Em 20/10/2026 o responsável pela FM autorizou explicitamente a **Evolution API também** como opção de integração para o AtendeVendeIA.

A auditoria do código confirmou:
- O canal Meta existente reside em `channels/whatsapp.py`, `channels/whatsapp_send.py`, `channels/meta_api.py` e em `api/routes/whatsapp.py`.
- Já existem as portas `MessageSender` e `ConnectionTester`, outbox, RLS/tenant, consentimento, opt-out, controles de recuperação e auditoria; **reutilizá-las**.
- `providers/catalog.py` registra somente `whatsapp_cloud` para WhatsApp; `recovery/engine.py` e `channels/outbox.py` referenciam `whatsapp_cloud` diretamente. **Adicionar apenas um cliente HTTP não entrega uma integração funcional.**
- `FM_WHATSAPP_LIVE` seleciona hoje somente o sender da Meta; habilitá-lo não significa Evolution disponível.
- A infraestrutura Contabo apresenta NPM/Evolution em recuperação após incompatibilidade do Compose V1; a existência de contêineres/volumes não prova conexão, versão ou estabilidade. Não reinicializar contêineres nem volumes sem inventário/backup.

## Decisão aprovada

1. **Manter Meta Cloud API como opção existente e admitir Evolution API como segundo provedor WhatsApp**, usando um **adaptador plugável dentro da mesma API e worker canônicos**. Nenhuma reconstrução do AtendeVendeIA nem duplicação de conversas, filas, autenticação, persistência, autoridade de tenant, regras comerciais ou canal social.
2. A identificação do provedor e suas credenciais são **separadas por tenant e conexão**; implementar seleção explícita de conexão ativa para o mesmo canal WhatsApp. Não presumir conexão global ao número de todos os clientes nem permitir envio para instância de outro tenant.
3. Utilizar o domínio canônico para receber, normalizar e persistir mensagens e status (IDs de evento/telefone/instância estáveis). Preservar deduplicação, idempotência, RLS, opt-out, janela legal/políticas de contato, limites e erro de resultado incerto sem retry inseguro.
4. **Segredos e webhooks:** manter API key/instance token somente em cofre/config cifrada, nunca na URL/log/UI. Validar autenticação de origem com mecanismo **comprovado na versão exata da Evolution** (ex.: header de autorização/token segregado por instância), escopo tenant+instância e proteção anti-replay. Verificar HTTPS, DNS/SSRF, tamanho de payload, spoofing e rate limiting; recusar evento em dúvida (**fail closed**).
5. **Seleção do protocolo aprovada em 20/10/2026:** o Diretor escolheu **Evolution API + WhatsApp Web/Baileys com pareamento por QR Code** para o **AtendeVendeIA**. A Evolution controla o protocolo, a sessão e fornece o QR Code; a **interface Web do próprio AtendeVendeIA** solicitará e mostrará o código ao dono/admin do tenant autenticado. **FM Command não é o gerador nem a interface de pareamento do cliente**: permanece autoridade comercial/licenças e supervisão consolidada, nunca autoridade da sessão WhatsApp. O QR e tokens não devem ser copiados ao Command ou a outras plataformas. A escolha da modalidade não elimina avaliação de risco de banimento/desconexão, limites/termos do WhatsApp, consentimento, recovery e homologação real. Meta Cloud API oficial permanece como opção preservada.
6. A ativação de envio real será **governada e desligada por padrão** até aprovação e certificação. Um teste simulado não implica estado `connected`, entrega, nem operação comercial.
7. O uso de uma **única infraestrutura Evolution** na VPS compartilhada não dá a ela direito de consultar dados de Kordena, NFCore ou da base do AtendeVendeIA. O gateway é borda de comunicação, não autoridade de clientes.
8. Preservar separadamente as regras do **VendeAtendeIA**: esta decisão/PR não altera seus repositórios, cronograma, escolhas de canal ou deploy.

## Responsabilidade pelo QR Code e separação de autoridades

1. **Evolution API na Contabo:** cria ou prepara uma instância por conexão de cliente, solicita o pareamento por WhatsApp Web e entrega um QR Code temporário pela API. Os endpoints e formato exatos serão congelados somente depois de identificar a versão real instalada. O Evolution Manager poderá ser usado pela operação FM em diagnóstico, mas NÃO será a interface de onboarding oferecida ao cliente.
2. **Backend canônico AtendeVendeIA:** identifica `tenant_id` a partir de sessão válida e RBAC owner/admin, gerencia vínculo instância-tenant, chama a Evolution por rede interna autenticada, aplica TTL e rate limit, devolve somente o QR temporário para o respectivo dono/admin, confirma estado de conexão e audita rotação/revogação. Jamais expor chave global/instance token ao navegador ou permitir que outro tenant solicite QR alheio.
3. **Frontend Web existente AtendeVendeIA:** apresenta botão **Conectar WhatsApp**, exibe o QR Code retornado pelo backend e informa conexão pendente/conectada/desconectada com base no estado verificado. Atualização e expiração do QR devem ser controladas. O cliente lê o QR no celular em WhatsApp → Aparelhos conectados → Conectar aparelho; nenhum cliente necessita credenciais da VPS.
4. **FM Command:** recebe apenas observabilidade governada/agregados permitidos e dados de licença; não hospeda QR, não armazena sessões, não gerencia instâncias diretamente nem muda tenancy. Qualquer ação administrativa excepcional futura exige novo contrato e aprovação, não se deduz desta ADR.
5. **Falhas e segurança:** JWT/sessão e RBAC no backend; origem/webhooks autenticados, instância escopada, não confiar em headers tenant do cliente, proteger dados de pareamento de logs/cache/observabilidade; fail closed sem credenciais válidas, sessão não comprovada, QR expirado ou reconnect incorreto. Eventos de mudança de estado não provam entrega de mensagem. QR Code não é requisito para a modalidade Meta Cloud API.

## TARGET de implementação e critérios de aceite

As mudanças devem ser planejadas a partir do CURRENT do GitHub; não criar duplicação de API, outbox ou login.

- **Jornada de QR Code:** adaptar a área existente de conexões do AtendeVendeIA para owner/admin autenticados, consumir somente API do próprio backend e usar a Evolution para gerar/renovar QR temporário e checar sessão, sem compartilhar credenciais ou QR com FM Command. Testar tenant crossover, expiração, desconexão/reconexão e não vazamento em cache/logs.
- **Catálogo e armazenamento:** adicionar `evolution` (ou identificador contratual decidido na implementação) a `providers/catalog.py`; campos de URL interna aprovada, instância/número, segredo e webhook. Validação estrita e máscara de segredos; persistência no mesmo `connections`/RLS e histórico de auditoria.
- **Porta HTTP:** implementar cliente Evolution v2 com timeouts, controle de erros 4xx versus resposta incerta (timeout/5xx) e limites de egress. Confirmar os contratos reais do endpoint de texto, mídias, status e sessão antes de travar versão.
- **Entrada webhook:** caminho explícito próprio (não reutilizar endpoint da Meta), validação de autenticação e escopo da instância **antes** de carregar/salvar mensagem; parser validado contra fixtures da versão escolhida. Eventos não suportados devem ser ignorados ou rejeitados de forma governada sem vazar dados.
- **Roteamento de saída:** integrar a seleção ativa Evolution/Meta ao `MessageSender` e aos pontos atualmente fixos em `recovery/engine.py` e `channels/outbox.py`, preservando a porta existente e a semântica de envio no máximo uma vez; nunca enviar pelo fallback Meta após falha de Evolution.
- **Testador real:** vincular teste de conexão à instância/número real; `connected` apenas após sucesso comprovado, incluindo revogação/desconexão; sem token/instância válidos => `needs_attention`.
- **Templates e políticas:** reconciliar recuperação e templates Meta com as capacidades legais/técnicas do modo Evolution escolhido; não declarar aprovação de template inexistente; bloquear disparos que dependam desse status.
- **Testes internos:** Ruff, mypy, unitários, integração com PostgreSQL e RLS, tentativas de spoof de tenant/instância, webhook sem autenticação, repetição/replay, falha de rede, mensagens concorrentes, outbox, opt-out, bloqueio de licença e isolamento entre produtos; CI e governança verdes.
- **Homologação externa:** somente após a construção estar pronta, validar com uma versão exata da Evolution, mensagem recebida/respondida de uma instância controlada, perda de sessão, reconexão, 5xx/retry/filas, webhooks reais, proxy HTTPS e observabilidade. Registrar evidências, riscos e aprovação humana antes do modo live.

## Restrições e riscos

- Evolution API é um **provedor intermediário**, não substitui a política da Meta; usar Baileys/WhatsApp Web pode ter risco adicional de bloqueio, desconexão e limitações de suporte oficial.
- API key isolada por tenant e instância não pode ser assumida: **verificar suporte real do provedor**, especialmente se a API usar chave global. Se a credencial tiver escopo global, o backend deve impor controles rígidos de instância e tenancy e minimizar privilégios.
- Nunca abrir a API de gerenciamento Evolution diretamente para clientes anônimos; Nginx Proxy Manager e Cloudflare não bastam como autenticação.
- Evitar modificações destrutivas na Contabo, onde Docker Compose V1 já gerou o erro `ContainerConfig`; recuperar rede, volumes e backup primeiro.
- Esta ADR **não muda configurações, credenciais ou habilita envios** por si só. Mantém `FM_WHATSAPP_LIVE=false` na configuração atual de implantação enquanto a integração não estiver certificada.

## Próxima etapa

Revisar contracts e impacto da Evolution v2 usando a instalação e a versão realmente disponíveis, implementar adaptador e testes na aplicação canônica, submeter em PR separada, executar CI e documentar evidências. Merge e deploy exigem autorização específica conforme regra 10 do `AGENTS.md`.
