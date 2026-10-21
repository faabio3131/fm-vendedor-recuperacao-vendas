# AtendeVendeIA — Pareamento Evolution por QR — checkpoint de construção

Status: **IMPLEMENTAÇÃO CANDIDATA / NÃO CERTIFICADA PARA PRODUÇÃO**  
PR: #73 (Draft). Base: PR #72 (ADR-0006 + regra 7 do AGENTS.md), sem merge.
Owner do escopo: AtendeVendeIA — repositório `fm-vendedor-recuperacao-vendas`.
**FM Command e VendeAtendeIA não foram alterados.**

## Autoridades e fluxo

- Evolution v2/Baileys (Contabo): autoridade da instância, geração do QR e estado da sessão.
- Backend FastAPI AtendeVendeIA: sessão cookie + membership ativo + owner/admin + feature
  `channel.whatsapp`, vínculo tenant→instância, chamada autenticada à Evolution, cifragem/RLS,
  resposta QR volátil, auditoria e confirmação de estado.
- Frontend Web AtendeVendeIA: botão **Conectar WhatsApp**, exibição de QR PNG temporário,
  status verificado no backend, reconsulta e nova solicitação após expiração.
- FM Command: licenciamento, consumo e supervisão corporativa. **Sem QR e sem sessão WhatsApp**.

O backend reserva o vínculo na tabela `connections` existente antes de tentar criar a instância,
com nome determinístico derivado do tenant e nunca controlado por parâmetro do navegador.
Não há nova tabela, nova autenticação ou fonte de verdade. Após criar/preparar a instância,
o código usa `GET /instance/connect/{instance}` para solicitar a imagem fornecida pela Evolution.
O QR **não é armazenado em banco, Command, cache nem log**. A resposta expõe a imagem
temporária apenas ao dono/admin autenticado; `Cache-Control: no-store` é aplicado pela API.

A conexão só é classificada `connected` quando
`GET /instance/connectionState/{instance}` retorna **`instance.state=open`**
da instância esperada. Timeout/erro rebaixa para `needs_attention`, ausência para
`disconnected`; nenhum retorno de QR prova conexão.

## Configuração: desligada por padrão

- `FM_EVOLUTION_ENABLED=false` (obrigatório permanecer desligado até aprovação).
- `FM_EVOLUTION_API_BASE_URL`: HTTPS 443, sem path, query, fragment ou credenciais.
- `FM_EVOLUTION_API_KEY`: segredo de plataforma forte, externo ao repositório.
- `FM_EVOLUTION_TIMEOUT_SECONDS=8`.

A chave global nunca segue ao frontend. O backend bloqueia qualquer chamada quando
o provider não está habilitado. A aplicação existente mantém `FM_WHATSAPP_LIVE=false`.
Ligar o pareamento NÃO liga envio de mensagens.

## Testes e evidência exigida

Código de testes: `apps/api/tests/test_evolution_pairing.py` e
`apps/web/e2e/evolution-pairing.spec.ts`.

| Controle | Critério |
|---|---|
| G1 | Owner/admin somente; membro agent 403; plano sem feature 402 |
| G2 | Dois tenants nunca compartilham nome/instância/QR; spoof de header rejeitado pela sessão |
| G3 | QR vem do payload Evolution; sem persistência/segredo/log; PNG e tamanho validados |
| G4 | Somente estado `open` marca `connected`; 404/timeout/erro nunca marcam |
| G5 | Falha e reconexão; não duplicar criação da instância; revogação genérica bloqueada |
| G6 | API: Ruff, Mypy strict, Pytest com PostgreSQL/RLS, security tests |
| G7 | Web: lint, typecheck, build e Playwright; Meta não regrediu |
| G8 | CI remoto no **HEAD exato**, security review e auditoria de mudanças |
| G9 | Homologação real: versão Evolution instalada, API/QR reais, celular controlado e sessão validada |

**Situação da CI durante desenvolvimento:** GitHub Actions na PR #72 e primeiro
HEAD da PR #73 reportou `failure`; jobs `api` e `web` apareceram sem steps
de execução no retorno da API, e logs não estavam disponíveis. Isso NÃO comprova
erro de teste nem sucesso. Investigar provisionamento/quota/billing/autorização do
runner antes de declarar qualquer gate verde. Nenhuma homologação real foi executada.

## Pendências ainda separadas do recorte QR

1. Reconciliação técnica com a **versão exata** Evolution v2 na Contabo; HTTP 502 e
   acesso SSH negado aparecem no runbook da PR #71, ainda não sanados por esta PR.
2. Entrada por webhook Evolution **autenticada e com anti-replay**, parser, opt-out
   e tratamento de status de entrega. Não inferir segurança do webhook sem prova da versão.
3. Seleção canônica de provider WhatsApp ativa por tenant, integração à fila/outbox,
   `MessageSender`, Conversation Engine, recuperação e templates/políticas, sem fallback
   Meta silencioso. **Nenhum envio Evolution ficou habilitado nesta PR**.
4. Logout/revogação segura com confirmação remota e reconciliação, antes de permitir
   excluir o vínculo da instância.
5. Testes completos e CI; integração com infraestrutura PR #71; homologação e
   aprovação humana de merge/deploy/credenciais reais.

## Operação e não-go

- Não instalar/resetar containers, mudar DNS ou criar credenciais reais com base neste documento.
- Nenhum usuário recebe URL/tokens do painel Evolution ou acesso Contabo.
- Não assumir que o QR ainda está válido pelo contador local; a Evolution é autoridade.
- Não expor QR a agente, outro tenant, FM Command ou site público.
- Não habilitar automação de contato sem consentimento, opt-out e evidência técnica.
- Em qualquer inconsistência de instância, status ou autenticação, **fail-closed**.

Próximo bloco: executar gates internos no mesmo SHA via runner disponível ou ambiente
isolado autenticado; corrigir regressões. Em seguida continuar a construção dos
webhooks e roteamento do provider, sem repetição da interface de QR já implementada.


## Atualização de verificação local — 20/10/2026

- O candidato foi clonado em diretório temporário isolado no computador autorizado, sem modificar os projetos existentes.
- `ruff check` sobre os nove arquivos Python tocados: **PASS**, após remover import não utilizado.
- `mypy --strict` sobre os quatro arquivos Python novos/de teste: **PASS** depois da correção do tipo de retorno da rota.
- `compileall` dos novos módulos: **PASS**.
- `pytest tests/test_evolution_pairing.py -q` com `PYTHONPATH=src`:
  **1 PASSED, 4 ERRORS durante setup**, porque o PostgreSQL do PC rejeita a
  autenticação do usuário local de testes `fm_owner`. **Nenhum dos quatro
  testes de integração executou seu corpo.** Não alterar credenciais da instância
  existente por conveniência. Docker não está instalado na máquina verificada.
- **Causa real do GitHub Actions encontrada**:
  execução [#39525222347](https://github.com/faabio3131/fm-vendedor-recuperacao-vendas/actions/runs/39525222347):
  os jobs `api` e `web` não foram iniciados. A anotação oficial diz
  *“The job was not started because recent account payments have failed or your spending
  limit needs to be increased. Please check the 'Billing & plans' section in your settings”*.
  Isto é **bloqueio de billing do GitHub**, não evidência de erro dos testes.
- Corrigidas as duas não conformidades estáticas encontradas. Revisar novamente
  os gates no HEAD atualizado; não reutilizar CI histórica como certificação.
- Sem credenciais Evolution, mensagem real, banco produtivo, merge ou deploy.

**Gate de merge: bloqueado** até disponibilidade de runner, testes PostgreSQL/RLS,
frontend E2E, revisão de segurança e autorização específica do Diretor.
