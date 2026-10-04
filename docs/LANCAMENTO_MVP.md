# Checklist de lançamento do MVP

Atualizado em 03/10/2026. Complementa `docs/PENDENCIAS_EXTERNAS.md` (itens P*/C*) e
`docs/00_GATE_WEB_FIRST.md`. Nenhuma linha abaixo está concluída só porque o código existe: cada item
marca **o que prova** que está pronto. Aprovação final de lançamento é do Diretor (AGENTS.md, regra 10).

## 1. Escopo do MVP (proposta, aguarda confirmação)

Dentro: WhatsApp, Messenger e Instagram oficiais (Meta; a recuperação por template é só WhatsApp) · recuperação (carrinho, PIX, boleto, recusa, orçamento, conversa que
esfriou, registro avulso, planilha) · templates enviados e sincronizados pela Meta · vendedor IA básico
com limite mensal por plano · painel web · login Google · compra do SaaS na Cakto/Hotmart cria a conta.

Texto de venda do V1: "atende e recupera, conduzindo a venda até o link de pagamento". Não prometer
montagem de pedido na conversa, geração/cobrança de Pix, estoque, entrega nem confirmação automática de
pagamento (exceto a que vem da Cakto/Hotmart).

Fora (V2 ou depois): checkout na conversa, Pix, endereço e entrega (Assistant/Core), cobrança própria, anúncios (Google/Meta Ads), Tech Provider /
Embedded Signup (exige CNPJ), cognição vertical do Core/Assistant (ADR-0002).

## 2. Decisões do Diretor que bloqueiam

| # | Decisão | Sugestão | Estado |
|---|---|---|---|
| D1 | Nome comercial, domínio e marca | **AtendeVendeIA** (`atendevendeia.com.br`) | NOME DECIDIDO em 03/10/2026; registro do domínio, consulta de marca e perfis sociais pendentes (ação do Diretor) |
| D2 | Preços e limites por plano (pesquisa de mercado) | Plano de entrada: **R$ 97,90/mês no anual** e **R$ 149,90/mês no mensal** (Diretor, 03/10/2026). Demais planos e limites de respostas de IA: ver seção 2.1 | PARCIAL: preço de entrada definido; planos acima e limites PENDENTES |
| D3 | Quem paga as mensagens da Meta | O cliente, na própria conta Meta, controlando o próprio gasto | **DECIDIDO** em 03/10/2026 |
| D4 | Conexão do WhatsApp sem Tech Provider e sem CNPJ (cliente com CPF) | Conexão simples para o cliente; caminho depende do teste real (seção 2.2) | PENDENTE |
| D5 | Garantia e reembolso | Segue a regra de cada plataforma onde o produto for vendido (Cakto e Hotmart); sem regra própria no código | **DECIDIDO** em 04/10/2026; o texto na política de uso e nos termos fica para depois (P8) |
| D6 | Janela de atribuição da venda recuperada | **5 dias** após o fim da sequência (era 7); pesquisa na seção 2.3 | **DECIDIDO** em 04/10/2026 e aplicado no código |
| D7 | Sequências e textos padrão | Tempos mantidos (batem com o mercado); textos ajustados para não começar nem terminar com variável (seção 2.3) | **DECIDIDO** em 04/10/2026 e aplicado; a primeira reprovação real da Meta ainda pode pedir novo ajuste |
| D8 | Vendedor IA apresentado como "básico" no MVP | Lançar a versão básica (MVP) primeiro; a V2 vem depois com o Core | **DECIDIDO** em 04/10/2026 |
| D10 | Carência em atraso antes de suspender | 3 dias (dado em `plans.grace_days`, muda com `plan-grace`); vale para todos os planos até o Diretor decidir | **PROVISÓRIO**: confirmar o prazo |
| D9 | Hospedagem (rascunho: Render) | Render como rascunho (decisão do Diretor, 04/10/2026); falta criar o staging e validar o `render.yaml` | **DECIDIDO como rascunho**; staging PENDENTE |

### 2.1 D2: o que a pesquisa de 03/10/2026 mostrou (para decidir os demais planos)

Concorrentes olhados nos sites deles; preços mudam, reconferir antes de publicar.

| Concorrente | Preço | Limite | Mensagens da Meta |
|---|---|---|---|
| Atendente.AI | R$ 99, 499 e 999/mês (anual) | 1.000, 5.000 e 10.000 créditos de resposta de IA/mês | não cobra à parte |
| Chat Inteligente | R$ 149, 390, 690 e 1.490/mês | não informa | pode cobrar à parte |
| WiiChat | grátis (sem API) e R$ 379/mês | 1.000 contatos; implantação de R$ 2.999 | cobradas à parte |
| Clint | R$ 149 por usuário/mês (anual) | créditos por sessão, com teto de gasto | cobradas à parte |

- O padrão é limitar por **resposta de IA por mês**, igual ao `plans.limits` que já existe.
- Faltam: planos acima do de entrada, e o limite de respostas por plano. Dependem do custo real por
  resposta do Gemini (teste 1 da seção 4). Os valores no código seguem **provisórios**.
- Leitura adotada: R$ 97,90 é o valor mensal cobrado no plano anual; o plano mensal é R$ 149,90 (confirmado pelo Diretor).

### 2.2 D3 e D4: custo da Meta e conexão

- Desde 01/07/2025 a Meta cobra **por mensagem** (não por conversa): template de marketing é cobrado a
  cada envio; texto livre e template de utilidade dentro da janela de 24 h aberta pelo cliente são grátis.
  Tarifas do Brasil não foram conferidas; consultar a tabela da Meta antes de orientar o cliente.
- Portfólio Meta novo: limite de **250 contatos únicos por 24 h** (documentação da Meta).
- **Não confirmado** (só blogs de terceiros, nada na documentação da Meta): se pessoa com só CPF consegue
  criar e aprovar template de marketing; se a verificação da empresa no Brasil exige cartão CNPJ.
  Só a conta de teste da Meta (C1/C2) responde. Opções a avaliar nesse teste: (a) orientar quem não tem
  CNPJ a abrir MEI no guia; (b) verificar se o limite de 250/dia basta para começar.
- Conexão por QR code (tipo WhatsApp Web) **não será usada**: viola a regra 7 do AGENTS.md e arrisca o
  número do cliente.

### 2.3 D6 e D7: o que a pesquisa de 04/10/2026 mostrou

**D6, janela de atribuição.** Nenhum concorrente brasileiro consultado (Omnifox, Unnica, SocialHub, Yampi)
publica a janela. A única referência pública achada é a do Klaviyo (mais usado no mundo): atribui por
**último toque**, com **5 dias após o clique** em mensagem de WhatsApp e 12 horas após a abertura.
Diferença importante: o Klaviyo exige clique ou abertura; nós contamos pela **mensagem enviada** (não
rastreamos clique no V1). Nossa regra (`ATTRIBUTION_DAYS = 5` em `recovery/engine.py`, antes 7): a compra conta
se o caso está aberto ou se encerrou há até 5 dias, com ao menos uma mensagem enviada e o mesmo produto.
Contar só por envio é mais generoso que o mercado; janela menor protege a confiança do cliente no número.
Decisão do Diretor: **5 dias**, aplicado no código com teste de fronteira.

**D7, sequência e textos.** Os guias consultados convergem em **3 mensagens**: 30 min a 1 h (ajuda, sem
desconto), 24 h (dúvida comum ou valor novo, sem só repetir) e 48 a 72 h (última chamada, com prazo real);
parar assim que o cliente responde ou compra, e depois da terceira mensagem sem resposta. Nossos padrões
(carrinho: 30 min, 24 h e 3 dias; Pix: 15 min e 3 h; boleto: 1 e 3 dias) já seguem isso. Os números de
recuperação que os blogs citam (15 a 35%) são de marketing deles, não medidos: não usar em texto de venda.
Pontos a tratar nos textos: (a) vários terminam em `{link}`; há relatos de que a Meta rejeita variável no
começo ou no fim do corpo (não confirmado na documentação). Decisão do Diretor: ajustar. Feito: nenhum texto padrão começa ou termina com variável (teste
`test_default_templates_*`). (b) a Meta não aprova texto promocional em categoria utilidade: cupom e desconto vão em MARKETING.

### 2.4 Adaptadores: o que a documentação oficial confirmou (04/10/2026)

Isto é **leitura de documentação**, não teste com conta real. Cada linha diz o que mudou no código.

| Peça | Confirmado na documentação oficial | Ainda NÃO confirmado |
|---|---|---|
| Cakto: assinatura | Cabeçalho `X-Cakto-Signature: v1=<HMAC-SHA256>` sobre `"{X-Cakto-Timestamp}.{corpo bruto}"`, tolerância de 5 min; alternativa: campo `secret` no corpo. Código aceita as duas; os palpites antigos (`Authorization: Bearer`, `X-Cakto-Secret`) foram removidos | Qual chave entra no HMAC (a doc não diz; assumimos o segredo do webhook). Se estiver errada, o `secret` do corpo ainda autentica |
| Cakto: eventos e campos | Envelope `{secret, event, data}`; pedido em `data.id/customer/product/offer/amount/checkoutUrl`; carrinho abandonado tem formato próprio (`customerName/Email/Cellphone`, sem `id` nem `amount`); `data` pode ser lista (Webhook V2): usamos o pedido `main`; valores em reais (`5.0` = R$ 5); boleto usa `boleto.boletoUrl` | Se a conta real manda exatamente isso; `pix.qrCode` não é usado como link (é o código Pix) |
| Cakto: repetição | Dedupe por `data.id`, ou e-mail + oferta + `createdAt` no carrinho. A chave inclui o nome do evento (o mesmo pedido gera Pix gerado e depois compra aprovada). Retentativas só ocorrem por rede/timeout; resposta não-2xx **não** é reenviada pela Cakto | Eventos `picpay_gerado` e `openfinance_nubank_gerado` e `refund_requested` não são tratados (ignorados) |
| Cakto e Hotmart: status da conexão | Não há como consultá-las: o botão "Testar conexão" agora diz "ainda não chegou evento válido" (pendente) e **nunca** rebaixa uma conexão já confirmada por evento real | — |
| Hotmart | **Nada.** A página de documentação respondeu 403 ao nosso acesso e não foi contornada. Nomes de evento e campos continuam de fonte de terceiros | Tudo: cabeçalho `X-Hotmart-Hottok`, nomes de evento, caminhos dos campos. Precisa da documentação colada pelo Diretor ou de um evento real |
| Gemini | Modelo `gemini-3.8-flash` existe e está em disponibilidade geral; `generateContent` segue suportado (a Google recomenda a Interactions API para projetos novos, sem data de desativação); chave no cabeçalho `x-goog-api-key`; `thinkingLevel` aceita `low/medium/high` (`minimal` dá erro neste modelo; padrão é `medium`); documentação manda deixar `temperature` no padrão. Código: removida a temperatura, raciocínio `low` (`FM_AI_THINKING_LEVEL`), limite de saída 1024, raciocínio somado à saída no medidor de custo | Se `maxOutputTokens` inclui o raciocínio; se `candidatesTokenCount` já inclui o raciocínio; se o `responseSchema` com tipos em maiúsculas é aceito. O `ai-check` responde as três |

**Preço do Gemini 3.8 Flash (dado com data, fonte: ai.google.dev, 04/10/2026):** US$ 0,75 por milhão de tokens de
entrada e US$ 3,75 por milhão de saída **até 31/12/2026**; US$ 1,50 e US$ 7,50 **a partir de 01/01/2027**. O preço sobe
em 3 meses: ao definir limites e planos (D2), usar o valor de 2027.

## 3. Contas e ações que destravam os testes

- [ ] Client ID do Google (P1) · [ ] chave do Gemini no staging (P2) · [ ] hospedagem e domínio (P3)
- [ ] papéis do Postgres e chave de cifragem em cofre, com cópia separada (P4)
- [ ] produto de teste na Cakto (P6) e na Hotmart (P7) · [ ] conta de teste da Meta com número e token (C1/C2)
- [ ] termos de uso, privacidade e LGPD revisados por advogado (P8) · [ ] orientação do contador (P9)

## 4. Testes com contas reais, nesta ordem

Cada teste só vale se registrar o resultado real (data, o que foi enviado, o que voltou).

1. **Gemini:** `python -m fm_seller.cli ai-check` termina com `RESULTADO: OK`; anotar nome do modelo aceito,
   formato de resposta e custo por resposta (revisar `plans.limits`).
2. **Login Google real** em staging; e-mail não verificado é recusado.
3. **Conexão do WhatsApp:** com `FM_WHATSAPP_LIVE=true` em staging, "Testar conexão" confirma token,
   número e conta. Token errado deve falhar sem vazar o token.
4. **Webhook de entrada:** handshake, assinatura, mensagem recebida, status de entrega, "SAIR". Repetir para
   **Messenger e Instagram** (página e conta de teste; mensagem recebida, resposta dentro de 24 h, entrega e leitura,
   "SAIR"; conferir o formato do envio, o ID de quem escreve e se a janela é mesmo de 24 h).
5. **Templates:** enviar `pix_1` pelo painel, conferir status na Meta, categoria devolvida, motivo de
   reprovação (atenção: texto que começa ou termina com variável) e corrigir os textos padrão.
6. **Envio:** texto dentro da janela de 24 h e template fora dela, com parâmetros; conferir que o
   passo vira `sent` só com id devolvido e que erro incerto vira `failed` sem reenvio.
7. **Compra de teste Cakto/Hotmart:** cria cliente, plano e convite; capturar o evento real e comparar com a
   seção 2.4. Cakto: o código já segue a documentação; conferir se a assinatura `X-Cakto-Signature` valida
   (se não, só o `secret` do corpo está valendo). Com `FM_CAPTURE_EVENTS=true` o campo `auth_method` do evento
   capturado responde isso (`assinatura_hmac` ou `segredo_no_corpo`) e `capture show` confere os campos. Hotmart: **corrigir os caminhos de campos** de
   `events/normalize.py` (ainda são suposições). Conferir também os eventos de **assinatura** (atraso, recuperação,
   cancelamento, reembolso): nomes, ordem e repetição reais; o ciclo de vida (`provisioning/lifecycle.py`) só
   aceita as transições previstas e ignora o resto.
8. **Relatório:** depois de uma compra de teste recuperada de verdade, conferir se o valor e a data do relatório batem com
   o que a plataforma registrou (a atribuição segue D6; nada foi comparado com venda real ainda).
9. **Ponta a ponta:** compra → login → conectar WhatsApp → template aprovado → oportunidade → mensagem
   recebida pelo contato → resposta do contato encerra o caso.

## 5. Prontidão operacional (gate Web First, itens 7–11)

- [ ] Staging com `FM_ENV=staging` subindo sem erro de configuração; `/health` ok; migrations aplicadas.
- [ ] Worker rodando (um ciclo por 30 s) e log do ciclo visível. *(batimento e `ops-check` prontos; falta rodar no staging)*
- [ ] Backup diário com teste de restauração em banco separado (a chave de cifragem também, em separado). *(scripts prontos e testados localmente; falta agendar, guardar fora do servidor e restaurar no ambiente real)*
- [ ] Alertas mínimos: webhook parado, fila de saída crescendo, falha de envio, falha de sincronização. *(`ops-check` cobre tudo menos webhook parado e API fora do ar: ver OPERACAO.md; falta ligar o aviso ao e-mail)*
- [ ] Rollback definido (reverter imagem e migrations compatíveis). *(regra em OPERACAO.md; o rollback do **banco** foi ensaiado em Postgres local com `scripts/ops/rehearsal.sh`, também nos testes; falta ensaiar a volta da imagem no staging)*
- [ ] `preflight` sem crítico e `smoke` sem crítico no ambiente que vai ao ar. *(comandos prontos e testados contra servidor simulado e banco local; falta rodar no ambiente real)*
- [ ] CI verde (api, web, e2e) no commit que vai ao ar.

## 6. Critérios de go / no-go

**Go** somente se: testes 1 a 8 da seção 4 registrados · decisões D1–D6 tomadas · termos e privacidade
publicados · prontidão da seção 5 completa · nenhum segredo no repositório (`git log -p` revisado).

**No-go** se qualquer um destes ocorrer: envio real nunca testado com conta Meta · formato de evento de
compra não confirmado · restauração de backup nunca testada · token ou credencial aparecendo em log ou tela.

## 7. Risco residual conhecido

O formato de envio, a criação de template e o teste de conexão foram escritos pela documentação da Meta e
testados só contra servidor falso. O adaptador do Gemini também (conferido na documentação oficial, seção 2.4,
mas nunca chamado com chave real). O formato da Cakto foi alinhado à documentação, sem evento real; o da Hotmart
nem isso. Até os testes da seção 4, trate-os como **não validados**.
