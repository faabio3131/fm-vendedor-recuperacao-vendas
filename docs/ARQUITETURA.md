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

## Eventos e recuperação (Bloco 2)

`webhook → ingest (segredo, dedupe, payload cifrado) → normalize → handle_event → caso + passos agendados →
worker (checagens na hora do envio) → MessageSender`. O evento bruto é gravado antes de qualquer decisão;
falha de tratamento deixa o evento `failed` para reprocesso. O worker usa o modo `system` do banco
(rotina de plataforma), nunca em rota de usuário. Compras do próprio SaaS entram por rota separada
(`/v1/platform/webhooks/*`) e criam cliente/plano/convite de forma idempotente (lock por e-mail).

## Conversas e vendedor IA (Bloco 3)

`WhatsApp (assinado) → conversa/mensagem (dedupe pelo id do provedor) → worker: vendedor IA → fila de saída →
MessageSender.send_text`. O vendedor responde numa transação que também marca a mensagem como tratada, e a
saída segue a regra de no máximo uma vez. O modelo recebe só persona, ofertas e histórico; nunca credenciais.

## Limites (Bloco 6)

Limite de uso da IA é dado do plano (`plans.limits`), checado antes de cada chamada ao modelo; uso diário
agregado em `ai_usage`. O limite de contatos novos por número é por cliente (`tenant_settings`) e é
reconferido na hora do envio, junto das demais travas da recuperação.

## Modelo de IA (Bloco 5)

`ai/gemini.py` implementa `AiModel` com saída JSON estruturada. O prompt de sistema traz regras fixas, as
ofertas (id, nome, descrição; **sem preço nem link**) e a persona do cliente como mero tom de voz; as
falas do cliente entram só como turnos de usuário. A resposta é validada (campos, tipos, `finishReason`) e
ainda passa por `render_reply`, que recusa URL, valor em reais e marcador sem oferta. Qualquer falha vira
transferência para pessoa. A chave é da plataforma (custo entra no preço do plano); uso por cliente ainda
não é medido.

## Oportunidades próprias (Bloco 4)

O motor não depende de checkout. `Opportunity` (gatilho, origem, referência, contato, produto, valor) é a
entrada única de `open_opportunity`, usada por: eventos de checkout (`source=checkout`), conversa que
esfriou (`conversa`, detectada pelo worker em `recovery/cold.py`), registro avulso (`manual`) e planilha CSV
(`importacao`). Todas passam pelas mesmas regras (consentimento, opt-out, janela de silêncio, limites,
template aprovado). A conversa que volta a escrever encerra o caso `conversa`; o lojista encerra qualquer
caso com "vendido" (conta como recuperada só se alguma mensagem já saiu) ou "perdi". Cakto e Hotmart do
cliente são apenas mais uma fonte; a Cakto/Hotmart da F&M servem só para vender o SaaS (compra cria a conta).

## Portas (para os próximos blocos)

`GoogleVerifier`, `ConnectionTester`, `MessageSender`, `AiModel` e, a seguir, canais de mensagem, IA, checkout e pagamento.
Cada porta tem versão simulada para dev/teste e adaptador real separado.
