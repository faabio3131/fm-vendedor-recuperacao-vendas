# Continuidade entre conversas e aparelhos

Para quem abrir uma conversa nova (por exemplo no PC) e precisar retomar de onde parou. O repositório é
**público** desde 04/10/2026 (decisão do Fábio, para liberar o GitHub Actions): chaves, senhas, tokens,
e-mail ou telefone reais, nome de cliente e endereço de ambiente real **nunca** entram no git, nem em
documento, fixture, commit ou PR. O link da conversa abaixo só abre com a conta do Fábio.

## Conversa de origem (cloud)

Link da sessão do Engenheiro Sênior que construiu os Blocos 1 a 10C:
<https://claude.ai/code/session_01XHjB6bTVBd61bSzPXR3v8a>

Se a conversa nova não conseguir abrir o link, ela deve ler o repositório (`AGENTS.md`, `README.md` e os
documentos listados abaixo) e continuar a partir do estado descrito aqui.

## Leia nesta ordem

1. `AGENTS.md`: regras obrigatórias (nenhum merge ou deploy sem aprovação do Fábio; nenhum segredo no git).
2. `docs/LANCAMENTO_MVP.md`: escopo, decisões D1 a D9, testes com contas reais, go/no-go.
3. `docs/PENDENCIAS_EXTERNAS.md`: contas e ações do Fábio.
4. `docs/STAGING_RENDER.md`: criar o ambiente de construção (Render grátis).
5. `docs/OPERACAO.md` e `docs/ARQUITETURA.md`.

## Estado em 04/10/2026 (tarde)

- Construídos (contra simuladores): Blocos 1 a 10C, 11 (Messenger e Instagram), 12 (ciclo de vida da assinatura),
  13 (relatórios de recuperação), 14 (captura segura de eventos reais) e 15 (prontidão de subida). O prompt mestre
  `docs/PROMPT_MESTRE_BLOCOS_11_A_15.md` está concluído; cada bloco entrou no `main` pela sua PR com o CI verde.
- Em andamento: `docs/PROMPT_MESTRE_BLOCOS_16_A_20.md` (16 primeiros passos, 17 qualidade do vendedor IA, 18 LGPD,
  19 segurança, 20 operação da plataforma). Os Blocos 16 (primeiros passos), 17 (qualidade do vendedor IA), 18 (privacidade e LGPD) 19 (segurança e limites de abuso) e 20 (operação da plataforma) estão feitos: o prompt mestre dos Blocos 16 a 20 está concluído.
- **GitHub Actions:** ficou bloqueado por cobrança da conta (04/10/2026) e foi **liberado** quando o repositório
  virou público; o CI (`api`, `web`, `e2e`) roda normalmente e é condição de qualquer merge.
- Ainda **nada** foi validado com conta real (Render, Google, Meta, Gemini, Cakto, Hotmart).
- **Render em suspenso, por decisão do Fábio:** construir primeiro, sem gastar, e subir ao servidor só no
  final para os testes. O blueprint `atendevendeia-construcao` foi criado no Render, mas o banco grátis não
  foi criado (o Render permite um banco grátis por workspace e a vaga já está ocupada por outro projeto do
  Fábio). `fm-seller-api` ficou em falha de deploy e `fm-seller-web` subiu; **os dois foram suspensos**
  (retomar com "Resume Web Service"). Não há URL pública em uso. O banco e o painel de outro projeto no mesmo
  workspace não devem ser tocados.
- Para subir de verdade, decidir o banco: pago (a partir de cerca de US$ 6/mês) ou Postgres grátis fora do
  Render (precisaria permitir criar o papel `fm_app`; não testado).
- **Fase atual (05/10/2026):** `docs/PLANO_FASE_STAGING.md`. Parte A (documentos, registro de testes, ensaio local) sem conta e sem custo; Parte B
  (staging no Render grátis e testes com contas reais) só com o "go" do Fábio, item a item. Decisão: Render grátis só para o staging; produção em
  outro provedor com região em São Paulo, escolhido depois. O plano grátis **não tem worker**: o que depende dele (vendedor IA, recuperação,
  fila de saída) não é testável ali (ver `docs/STAGING_RENDER.md`).
- **FM Command (05/10/2026):** o Fábio tem um centro de controle próprio (repositório `FM-CONTROL-CENTER`, só lido por este agente). O AtendeVendeIA expõe
  `/v1/control-plane/fmcc/*` (ADR-0004, `docs/FM_COMMAND_INTEGRACAO.md`); o conector do lado do FM Command precisa ser feito no repositório dele, com a
  autorização do Fábio. Nada conectado de verdade ainda.
- Textos jurídicos em rascunho em `docs/juridico/` (P8); faltam o advogado e o provedor em São Paulo.
- Cópia da documentação no Google Drive: pasta "AtendeVendeIA (cópia do projeto)", **só os documentos do
  repositório** (prompts mestres não vão). É um retrato; refazer quando um bloco fechar.

## Esclarecimento de escopo (04/10/2026)

O Fábio deixou claro que o SaaS tem **todas** as funções (atendimento, venda e recuperação de carrinho,
Pix e boleto). Cakto e Hotmart têm dois papéis separados:

1. **Onde vendemos o AtendeVendeIA.** Não exige código. O único código é a criação automática da conta de
   quem compra (`/v1/platform/webhooks/*`); sem ele, cria-se a conta com `create-tenant`.
2. **Fonte de dados dos clientes que vendem lá.** Os adaptadores de checkout (`events/`) recebem
   carrinho abandonado, Pix e boleto para a recuperação.

O código não deve depender de onde o SaaS é vendido. Uma tentativa de registrar "Cakto/Hotmart só como canal
de venda" (PR #15) foi um erro de leitura e foi fechada sem merge.

## Próximos passos

Agora: seguir `docs/PLANO_FASE_STAGING.md` (cada PR só entra com o CI verde). Cobrança própria e anúncios ficam fora por ora.
Testes só com simuladores até o item B1 do plano, e só com o "go" do Fábio.

Do Fábio, na subida ao servidor: escolher o banco; retomar ou recriar o ambiente no Render pelo guia
`docs/STAGING_RENDER.md`; registrar `atendevendeia.com.br`; criar o Client ID do Google; colar a documentação
ou um evento de exemplo da Hotmart (a página oficial deu 403 e não foi contornada). Depois, com as contas:
testes reais da seção 4 de `docs/LANCAMENTO_MVP.md`.

## Combinados de trabalho

- O Fábio decide; o Engenheiro Sênior analisa, propõe, executa e documenta.
- Nunca declarar sucesso sem verificação; marcar como "não confirmado" o que não foi visto com conta real.
- Cada PR só entra no `main` com o CI verde e com a aprovação do Fábio (nos prompts mestres, a autorização dele
  para o merge está escrita no próprio prompt).
