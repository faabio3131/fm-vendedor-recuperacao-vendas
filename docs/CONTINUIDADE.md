# Continuidade entre conversas e aparelhos

Para quem abrir uma conversa nova (por exemplo no PC) e precisar retomar de onde parou. Repositório
**privado**; o link abaixo só abre com a conta do Fábio. Nada aqui é segredo: chaves, senhas e tokens
**nunca** entram neste arquivo.

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

## Estado em 04/10/2026 (fim da manhã)

- No `main`: Blocos 1 a 10C. Último merge: PR #16 (Render em modo construção, `cli bootstrap`).
- Aberto: PR #14 (Bloco 10B, adaptadores Cakto e Gemini alinhados à documentação oficial), CI verde,
  aguardando aprovação do Fábio.
- Ainda **nada** foi validado com conta real (Render, Google, Meta, Gemini, Cakto, Hotmart).
- Cópia da documentação no Google Drive: pasta "AtendeVendeIA (cópia do projeto)". É um retrato; refazer
  quando um bloco fechar.

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

Do Fábio: aprovar o PR #14; criar o ambiente no Render pelo guia; registrar `atendevendeia.com.br`; criar o
Client ID do Google; colar a documentação ou um evento de exemplo da Hotmart (a página oficial deu 403 e
não foi contornada). Depois, com as contas: testes reais da seção 4 de `docs/LANCAMENTO_MVP.md`.

## Combinados de trabalho

- O Fábio decide; o Engenheiro Sênior analisa, propõe, executa e documenta.
- Nunca declarar sucesso sem verificação; marcar como "não confirmado" o que não foi visto com conta real.
- Cada PR só entra no `main` depois de o Fábio aprovar.
