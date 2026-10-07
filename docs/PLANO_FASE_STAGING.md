# Plano mestre: fase de staging e testes com contas reais (AtendeVendeIA)

> Fonte única da verdade desta fase. Formato do kit padrão de plano mestre (campos fixos por item; o kit não está neste repositório). Nada aqui é
> segredo; o repositório é público. **Nunca** colocar chave, senha, token, URL real de ambiente, e-mail ou telefone no git.

## Cabeçalho

- **Objetivo:** subir um ambiente de staging no Render grátis e provar, com contas reais de teste, o que hoje só foi testado contra simuladores.
- **Dono / aprovador:** Fábio (Diretor). Aprovado em 05/10/2026 para a Parte A; a Parte B exige o "go" dele por item.
- **Estado:** EM EXECUCAO. Parte A concluída em 05/10/2026. Parte B: B1 concluído em 06/10/2026; B2 em diante aguardam o "go" do Fábio, item a item.
- **Decisão de hospedagem (05/10/2026):** staging no **Render grátis**, sem dado real de cliente. Produção será em outro provedor com região em
  São Paulo, escolhido depois; os textos jurídicos só dizem "São Paulo" depois disso.
- **Fora de escopo:** produção, dado real de cliente, cobrança própria, anúncios, e-mail/alerta externo, teste de invasão, qualquer plano pago.
- **Pode fazer sem perguntar:** itens A1 a A3 (só documentação, scripts e testes locais), com merge de PR quando os 3 checks (api, web, e2e) estiverem
  verdes no commit final, sem conflito e com `Merge PR #N (aprovado por Fábio)`.
- **Deve parar e perguntar:** qualquer criação ou retomada de recurso no Render ou em outro servidor, qualquer conta real, qualquer chave, qualquer gasto,
  decisão de produto, preço, segurança, dado pessoal ou outro repositório.
- **Riscos conhecidos do plano grátis (documentação do Render consultada em 04/10/2026, conferir de novo):** banco grátis é um por workspace e o
  workspace do Fábio já tem um de outro projeto (**bloqueia** a criação); banco grátis expira em 30 dias; serviço web dorme após 15 min; **não há worker nem cron
  grátis**, então vendedor IA, recuperação, fila de saída e sincronização de templates **não rodam** no staging grátis; o usuário padrão do banco pode não conseguir
  criar o papel `fm_app` (o `bootstrap` para e avisa; nunca rodar a API como dono do banco).

## Itens (em ordem; um por vez)

### 1. A1: Atualizar os documentos para a decisão de staging

- [x] **Estado:** concluído em 05/10/2026
- **Objetivo:** `STAGING_RENDER.md`, `CONTINUIDADE.md` e `PENDENCIAS_EXTERNAS.md` refletem a decisão (Render grátis só para staging; produção em São Paulo depois).
- **Depende de:** nada
- **Entregar:** documentos atualizados; lista clara do que o plano grátis **não** consegue testar (tudo que depende de worker).
- **Não fazer:** criar recurso, mexer no Render, alterar código.
- **Critério de aceite:** um leitor novo entende em 2 minutos o que dá e o que não dá para testar no grátis; sem segredo nem URL real.
- **Verificação:** `git diff` só em `docs/`; CI verde na PR.
- **Riscos / não confirmado:** limites do Render copiados da documentação de 04/10/2026.
- **Decisões do dono pendentes:** nenhuma.
- **Prova:** PR #35, merge `ee29c87` (05/10/2026). CI api, web e e2e verdes antes do merge. Só `docs/` alterado.

### 2. A2: Registro de testes reais e roteiro de cada conta

- [x] **Estado:** concluído em 05/10/2026
- **Objetivo:** modelo `docs/TESTES_REAIS_REGISTRO.md` com uma linha por teste da seção 4 de `LANCAMENTO_MVP.md` (data, ambiente, o que foi feito, resultado real, o que voltou, decisão) e um passo a passo curto, em português simples, para o Fábio criar cada conta (Google Client ID, chave do Gemini, app de teste da Meta).
- **Depende de:** 1
- **Entregar:** o registro vazio e os roteiros; cada roteiro diz o que NÃO colar no chat (chave, token, senha).
- **Não fazer:** preencher resultado de teste que não aconteceu.
- **Critério de aceite:** os 9 testes da seção 4 estão no registro, na mesma ordem, cada um com "como provar" copiado de `LANCAMENTO_MVP.md`.
- **Verificação:** conferir manualmente contra a seção 4; CI verde.
- **Riscos / não confirmado:** telas das consoles do Google e da Meta mudam; os roteiros dizem "conferir na tela atual".
- **Decisões do dono pendentes:** nenhuma.
- **Prova:** PR #37, merge `f96baa4` (05/10/2026): `docs/TESTES_REAIS_REGISTRO.md` com os 9 testes da seção 4 na ordem, todos NÃO FEITO, e roteiros das contas. CI verde antes do merge.

### 3. A3: Ensaio local completo (sem servidor externo)

- [x] **Estado:** concluído em 05/10/2026
- **Objetivo:** provar, em Postgres local, que `bootstrap`, `preflight`, `smoke` e `rehearsal.sh` funcionam em sequência como o `STAGING_RENDER.md` descreve, usando o mesmo formato de variáveis que o Render usará.
- **Depende de:** 1
- **Entregar:** o resultado dos comandos registrado em `docs/TESTES_REAIS_REGISTRO.md` (seção "ensaio local"); correção de qualquer falha encontrada, com teste.
- **Não fazer:** subir no Render; usar chave real.
- **Critério de aceite:** `preflight` sem crítico, `smoke` com `RESULTADO: OK` ou `COM AVISOS` explicados, `rehearsal.sh` termina sem erro.
- **Verificação:** rodar os quatro comandos e colar a saída (sem segredo) no registro; ruff, mypy e pytest passam se houver código.
- **Riscos / não confirmado:** local não prova o Render (papel `fm_app`, repasse de variável de build, `$PORT`).
- **Decisões do dono pendentes:** nenhuma.
- **Prova:** Ensaio local registrado em `docs/TESTES_REAIS_REGISTRO.md` (PR #37). Rodados: `bootstrap`, `preflight` (dev e staging), `smoke` local (OK) e `rehearsal.sh` (ENSAIO OK). Achou e corrigiu um bug real do `preflight` (PR #36, merge `55e8e4c`, 2 testes novos que falham no código antigo; pytest 566 passaram, nenhum falhou ou pulado; ruff, format e mypy limpos).

### 4. B1: Banco e criação do staging no Render (PARAR: precisa do Fábio)

- [x] **Estado:** concluído em 06/10/2026
- **Objetivo:** ter API e painel no ar no Render grátis, com banco, `bootstrap` rodado e `smoke` OK.
- **Depende de:** 1, 2, 3 e do "go" escrito do Fábio
- **Entregar:** staging no ar; log do `bootstrap`; resultado do `cli smoke --api ... --web ...`; só o que o plano grátis permite.
- **Não fazer:** dado real; plano pago sem o Fábio decidir; mexer em banco ou serviço de outro projeto; rodar a API como dono do banco.
- **Critério de aceite:** passos 1 a 4 da seção "Passo 5" de `STAGING_RENDER.md` conferidos; `/v1/health` pelo painel devolve o mesmo JSON.
- **Verificação:** `cli smoke` e a lista do Passo 5, com o resultado no registro.
- **Riscos / não confirmado:** o banco grátis do workspace pode estar ocupado; o usuário padrão pode não criar `fm_app`. **Se qualquer um dos dois ocorrer, parar** e apresentar ao Fábio as saídas: Postgres grátis fora do Render, ou banco pago a partir de cerca de US$ 6/mês (confirmar o preço no painel).
- **Decisões do dono pendentes:** onde fica o banco; se aceita pagar; ele cria as chaves e as guarda em cofre.
- **Prova:** API e painel no ar no Render grátis (região Virginia) com o banco no Neon grátis. Em 06/10/2026, commit `64441be`: o `bootstrap` criou o papel `fm_app` pelo `neondb_owner` do Neon (a dúvida "o dono do plano grátis cria papéis?" tem resposta: **sim**), as migrations 0001 a 0012 estavam aplicadas e o primeiro cliente existia; `cli smoke --api ... --web ...` terminou em `RESULTADO: OK` nos 11 pontos; `/login` do painel responde 200 e `/v1/health` pelo painel devolve o mesmo JSON da API. Detalhe e o que ainda não está provado em `docs/TESTES_REAIS_REGISTRO.md` (seção "Staging no Render"). Três tropeços reais, todos de configuração e já documentados em `docs/STAGING_RENDER.md`: Docker Command antigo (status 127, PR #41), `FM_DATABASE_ADMIN_URL` com o usuário do app, e `FM_SECRETS_KEYS` fora do formato `id:base64`.

### 5. B2: Login Google real (PARAR: precisa do Client ID)

- [x] **Estado:** concluído com ressalva em 07/10/2026
- **Objetivo:** entrar no staging com a conta Google do Fábio; e-mail não verificado é recusado.
- **Depende de:** 4 e do Client ID criado pelo Fábio
- **Entregar:** resultado no registro (teste 2 da seção 4).
- **Não fazer:** colar o Client ID ou segredo no chat/git sem necessidade (o Client ID não é segredo, mas não ponha no repositório).
- **Critério de aceite:** login real funciona; conta fora da lista de teste é recusada.
- **Verificação:** passos manuais descritos no roteiro de A2.
- **Riscos / não confirmado:** tela de consentimento "Em teste" limita quem entra.
- **Decisões do dono pendentes:** nenhuma.
- **Prova:** em 07/10/2026 o Fábio entrou no painel do staging pelo botão "Fazer login com o Google" com a conta que é usuária de teste do projeto Google Cloud `AtendeVendeIA staging` (projeto novo, separado de qualquer outro): o painel abriu a visão geral do cliente de teste, com o e-mail dele e o Plano 1 ativo. **Não provado:** e-mail não verificado recusado; conta fora da lista de teste recusada (quem barra é o Google, na tela de consentimento "Em teste"). Antes de passar, o login falhou por três causas, todas corrigidas e registradas: conexão do banco morta após a pausa do Neon grátis (PR #43, pool confere a conexão), API dormindo no plano grátis devolvendo 502 (PR #44, o login espera e tenta de novo) e o segredo do cliente colado no lugar do ID do cliente (só o ID, que termina em `.apps.googleusercontent.com`, vai no Render).

### 6. B3: Gemini real (PARAR: precisa da chave; gasto pequeno e pago)

- [ ] **Estado:** parcial em 07/10/2026 (rodado no `gemini-3.1-flash-lite`; falta o `gemini-3.8-flash`, padrão do projeto)
- **Objetivo:** `ai-check` com `RESULTADO: OK`, depois `ai-eval --real` sem falha de segurança; anotar custo por resposta e revisar `plans.limits`.
- **Depende de:** 3 (pode rodar local, não precisa do Render) e da chave dada pelo Fábio
- **Entregar:** resultados no registro (teste 1 da seção 4); proposta de limites por plano com o custo medido e o preço de 2027 (D2).
- **Não fazer:** guardar a chave no git; rodar em volume.
- **Critério de aceite:** nenhuma falha de segurança; as três dúvidas do Gemini (`maxOutputTokens`, `candidatesTokenCount`, `responseSchema`) respondidas ou registradas como problema.
- **Verificação:** saída dos comandos no registro.
- **Riscos / não confirmado:** chamadas são **pagas** (centavos). Pedir o "go" do Fábio antes.
- **Decisões do dono pendentes:** preço e limites dos planos acima do de entrada (D2).
- **Prova (parcial):** em 07/10/2026, de um ambiente com acesso ao Gemini, `ai-check` com `gemini-3.1-flash-lite`: `RESULTADO: OK`; na tentativa de burlar as regras ele recusou ("não consigo alterar essas informações") e uma chamada mediu 346 tokens de entrada e 184 de saída em 8,6 s. `ai-eval --real` no mesmo modelo: 12 cenários, **0 falhas de segurança**, 1 aviso (uma chamada sem resposta do Google, que virou passagem para uma pessoa por `erro_do_modelo`). **Não provado:** o `gemini-3.8-flash` (padrão do projeto) devolveu 503 "alta demanda" ou tempo esgotado em todas as tentativas do dia, o mesmo com `3.7-flash` e `3.5-flash`; erros 503 não são cobrados. Os preços e a decisão de modelo estão em `docs/PENDENCIAS_EXTERNAS.md` (IA multi-provedor).

### 7. B4: WhatsApp, Messenger e Instagram reais (PARAR: precisa da conta Meta de teste)

- [ ] **Estado:** pendente
- **Objetivo:** testes 3 a 6 da seção 4: teste de conexão, webhook (handshake, assinatura, mensagem recebida, entrega, "SAIR"), template real e envio de texto/template.
- **Depende de:** 4 e da conta Meta de teste criada pelo Fábio
- **Entregar:** resultado no registro por teste; correções do código para o que a Meta devolver de diferente, cada uma com teste.
- **Não fazer:** ligar `FM_WHATSAPP_LIVE` sem o "go" do Fábio; enviar para contato real; usar número de cliente.
- **Critério de aceite:** cada teste registrado com o que foi enviado e o que voltou; nada marcado como funcionando sem resultado real.
- **Verificação:** roteiro de A2.
- **Riscos / não confirmado:** **o envio, a recuperação, a fila e a sincronização de templates dependem do worker, que não existe no Render grátis**: ou rodar o worker local contra o banco do staging (só se o Fábio autorizar e o banco aceitar conexão externa), ou aceitar que esses testes esperam o plano pago.
- **Decisões do dono pendentes:** como rodar o worker (local ou pago); conta e número de teste.
- **Prova:**

### 8. B5: Cakto e Hotmart reais (PARAR: precisa dos produtos de teste)

- [ ] **Estado:** pendente
- **Objetivo:** teste 7 da seção 4: compra de teste, captura do evento real (`FM_CAPTURE_EVENTS=true`), conferência dos campos e do ciclo de assinatura.
- **Depende de:** 4 e dos produtos de teste criados pelo Fábio
- **Entregar:** fixtures anonimizadas em `apps/api/tests/fixtures/real_events/`; correção dos caminhos da Hotmart com teste; registro do que a Cakto validou (assinatura HMAC ou só `secret`).
- **Não fazer:** colocar evento bruto, e-mail ou telefone no git; desligar a captura depois é obrigatório.
- **Critério de aceite:** a fixture passa pelo normalizador; só dado anonimizado no repositório.
- **Verificação:** `capture show`, `capture export` e os testes novos.
- **Riscos / não confirmado:** Hotmart sem documentação. Acordar a API antes do teste (`/v1/health`).
- **Decisões do dono pendentes:** colar a documentação da Hotmart, se conseguir.
- **Prova:**

### 9. C1: Relatório de prontidão e decisão de go/no-go parcial

- [ ] **Estado:** pendente
- **Objetivo:** consolidar o registro, atualizar `LANCAMENTO_MVP.md` (seção 5 e riscos), `PENDENCIAS_EXTERNAS.md` e `CONTINUIDADE.md`, e dizer com franqueza o que passou, o que falhou e o que ficou sem teste.
- **Depende de:** 1 a 8 (os que o Fábio liberou)
- **Entregar:** relatório final no formato do modelo; backup dos documentos no Drive (só documentos do repositório, nunca prompts).
- **Não fazer:** declarar "pronto para clientes" sem os testes 1 a 8 da seção 4 registrados.
- **Critério de aceite:** cada teste da seção 4 tem resultado real ou o motivo de não ter sido feito.
- **Verificação:** o registro tem as 9 linhas preenchidas ou justificadas; CI do `main` verde.
- **Riscos / não confirmado:** o que depender de worker, produção ou teste de invasão.
- **Decisões do dono pendentes:** go/no-go; trocar o plano para produção em São Paulo.
- **Prova:**

## Registro de execução

| Item | PR | Merge | CI do main | Verificado como | Pendências |
|---|---|---|---|---|---|
| A1 (item 1) | #35 | `ee29c87` | verde (conferir execução mais recente) | só `docs/`; CI verde na PR | nenhuma |
| A2 (item 2) | #37 | `f96baa4` | verde (conferir execução mais recente) | os 9 testes da seção 4 conferidos contra `LANCAMENTO_MVP.md` | nenhuma |
| A3 (item 3) | #36 e #37 | `55e8e4c`, `f96baa4` | verde (conferir execução mais recente) | comandos rodados em Postgres local; bug do `preflight` corrigido com teste | o ensaio local não prova o Render |

## Relatório final (ao concluir)

Entregas por item · PRs e commits de merge · CI do `main` · o que foi verificado e como · o que **não** está confirmado · pendências do Fábio ·
próximo passo recomendado.
