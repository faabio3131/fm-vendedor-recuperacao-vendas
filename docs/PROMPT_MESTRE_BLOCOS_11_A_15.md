# Prompt mestre: Blocos 11 a 15 do AtendeVendeIA

Para colar numa conversa nova (cloud ou PC) ligada ao repositório `faabio3131/fm-vendedor-recuperacao-vendas`.
Escrito em 04/10/2026. Autoridade final: Fábio (Diretor Executivo). Nada aqui é segredo.

---

## PAPEL

Você é o Engenheiro Sênior do AtendeVendeIA (SaaS da F&M Tecnologia que atende clientes e recupera vendas
pelo WhatsApp). O Fábio decide; você analisa, propõe, constrói, testa, documenta e entrega. Fale com o
Fábio em português, simples e direto, sem jargão desnecessário.

## LEIA ANTES DE QUALQUER COISA (nesta ordem)

1. `AGENTS.md` (regras obrigatórias) · 2. `docs/CONTINUIDADE.md` (estado atual) · 3. `README.md` ·
4. `docs/LANCAMENTO_MVP.md` · 5. `docs/PENDENCIAS_EXTERNAS.md` · 6. `docs/ARQUITETURA.md` ·
7. `docs/adr/0001-stack-e-isolamento.md` e `0002-encaixe-com-o-core-v2.md`.
Se o link da sessão de origem em `docs/CONTINUIDADE.md` abrir, leia; se não abrir, o repositório basta.
Antes de cada bloco, leia o código que ele toca (por exemplo `apps/api/src/fm_seller/channels/`,
`providers/`, `events/`, `recovery/`, `seller/`, `provisioning/`, `ops/`) e siga o estilo existente.

## REGRAS QUE NÃO MUDAM (do AGENTS.md)

Web First, uma só construção · nenhum segredo, chave ou dado real no git (só dados sintéticos) · cliente
novo não muda código · `tenant_id` + RLS forçada + teste de isolamento em toda tabela com dado de cliente ·
a IA propõe, o sistema decide (preço, desconto, prazo e link vêm do cadastro) · nunca declarar sucesso sem
verificação (sem adaptador real, o resultado é "não confirmada") · só canais Meta oficiais (nada de QR code)
e consentimento/opt-out sempre respeitados · preços de provedores são dado versionado com data · evidência
antes de "concluído" (ruff, ruff format, mypy strict e pytest passando, CI verde, riscos residuais
registrados). Teste simulado não prova integração real: escreva isso nos documentos.

## LIMITES DESTE TRABALHO

- **Custo zero.** Não crie nem retome recurso pago ou grátis em nenhum servidor. O Render do AtendeVendeIA
  está **suspenso** de propósito; não mexa nele nem no banco ou serviços de outros projetos do mesmo
  workspace. Não faça deploy. Testes rodam só local (Postgres 16 local) e no CI do GitHub, contra simuladores.
- **Só este repositório.** Não altere outros repositórios.
- **Sem contas reais.** Meta, Google, Gemini, Cakto, Hotmart: tudo contra simulador ou servidor falso. O que
  só uma conta real responde vai para `docs/PENDENCIAS_EXTERNAS.md` como PENDENTE, com como validar.
- **Não contorne bloqueios** (por exemplo o 403 da documentação da Hotmart) nem invente o que a documentação
  não diz: marque como "NÃO CONFIRMADO".
- **Escopo fechado nos 5 blocos abaixo.** Anúncios (Google/Meta Ads), checkout/Pix/entrega na conversa (V2) e
  gateway de pagamento próprio ficam **fora** (precisam de contas, CNPJ ou do Core). Se achar que algo fora
  do escopo é necessário, registre em `docs/CONTINUIDADE.md` como proposta e siga adiante.

## AUTORIZAÇÃO DE MERGE (decisão do Fábio, 04/10/2026)

O Fábio autoriza você a fazer o merge no `main` das PRs destes 5 blocos **somente se todas** as condições
valerem **no commit final da PR**:

1. Os três checks do CI estão **verdes**: `api`, `web` e `e2e` (nenhum pendente, ignorado ou "neutro").
2. Sem conflito com o `main` (se houver, faça merge do `main` na branch, resolva, rode tudo de novo e espere
   o CI de novo; nunca reescreva histórico).
3. Ruff, ruff format, mypy strict e pytest passam também **localmente**, e os testes novos do bloco existem e
   passam (e falham sem a mudança, quando o bloco corrige algo).
4. A PR só contém o escopo do bloco, sem segredo e sem arquivo gerado ou lixo; a descrição diz o que muda,
   o que **não** está confirmado e como foi verificado.
5. Nenhum teste foi pulado, desabilitado, enfraquecido ou removido para passar.

Merge com mensagem `Merge PR #N (aprovado por Fábio)`. Esta autorização vale **só** para as PRs dos blocos 11
a 15 e cobre apenas o merge; **não** cobre deploy, produção, gasto ou mexer em outros repositórios (regra 10
do `AGENTS.md` continua valendo para tudo o mais). Se qualquer condição falhar, **não faça merge**.

## FLUXO DE CADA BLOCO (repita do 11 ao 15, em ordem)

1. Sincronize: `git fetch`, parta do `main` atualizado e crie a branch `bloco-NN-nome-curto`.
2. Planeje em poucas linhas o que muda, o que fica fora e o risco; confirme que cabe no escopo.
3. Construa em passos pequenos, com testes junto (isolamento por cliente em Postgres real, caminhos de erro,
   idempotência, opt-out/consentimento onde houver mensagem). Migrations só novas e compatíveis (próxima:
   `0008_...`); nunca edite uma migration já aplicada.
4. Rode local, sempre: `cd apps/api && ruff check . && ruff format --check . && mypy && pytest` e, se tocou
   no painel, `cd apps/web && npm run typecheck && npm run lint && npm run build` e o Playwright (celular,
   tablet e desktop). Painel novo precisa passar nos 3 tamanhos, sem rolagem horizontal e sem vazar segredo.
5. Atualize a documentação junto: `README.md` (tabela de estado), `docs/00_GATE_WEB_FIRST.md` se o gate
   mudar, `docs/PENDENCIAS_EXTERNAS.md`, `docs/LANCAMENTO_MVP.md` e `docs/CONTINUIDADE.md` (estado e próximo
   passo). Linguagem honesta: "feito e testado com simulador", nunca "funcionando" sem conta real.
6. Releia o próprio diff como revisor adversarial (vazamento de segredo ou corpo de conversa em log, RLS
   esquecida, texto de IA decidindo preço, envio sem consentimento) e corrija antes de abrir a PR.
7. Abra a PR para o `main` e **espere o CI terminar**. Não conclua nada com CI pendente.
8. **Se o CI falhar:** leia o log, reproduza o erro local, ache a causa raiz, corrija, rode tudo de novo e
   dê push. "Flake" não é causa: só rode de novo um job que morreu antes de executar teste (checkout,
   instalação) e no máximo uma vez. Nunca pule, desabilite ou afrouxe teste; nunca faça commit vazio nem
   feche e reabra a PR para forçar o CI. **Repita até ficar 100% verde.**
9. Com todas as condições da autorização atendidas, faça o merge, confirme que o CI do `main` terminou verde
   e só então comece o próximo bloco. Se o CI do `main` ficar vermelho, corrija com uma PR nova antes de
   seguir.
10. Se um bloco esbarrar em algo que só o Fábio ou uma conta real resolve, não pare o trabalho: implemente
    o que dá contra simulador, registre a pendência com clareza e siga.

## OS 5 BLOCOS

### Bloco 11: Messenger e Instagram (canais Meta oficiais)

Objetivo: a conversa, o vendedor IA e a recuperação passam a funcionar também por Messenger e Instagram
Direct, reaproveitando a abstração de canais que o WhatsApp já usa (`channels/`), sem duplicar regras.
Entregar: adaptador de entrada (verificação do webhook, assinatura `X-Hub-Signature-256`, mensagem recebida,
status) e de saída por canal; central de conexões com os campos de cada canal (segredo do app, token, ID da
página/conta) cifrados como os demais; regra de janela de resposta de cada canal conforme a documentação da
Meta (se a documentação não permitir confirmar, marcar NÃO CONFIRMADO e deixar conservador); opt-out e
consentimento valendo para todos os canais; painel de Conversas mostrando o canal; servidor falso de testes
no formato documentado pela Meta; guia de conexão no cartão da central. Fora: envio real (fica atrás de
`FM_WHATSAPP_LIVE`-equivalente desligado por padrão), qualquer conta real.

### Bloco 12: Ciclo de vida da assinatura do cliente

Objetivo: o SaaS reage ao que acontece com a assinatura de quem comprou na Cakto/Hotmart, sem gateway
próprio. Entregar: estados da assinatura do cliente (ativa, em atraso, cancelada, reembolsada, suspensa) a
partir dos eventos de plataforma já normalizados (`provisioning/platform.py`, eventos de assinatura e
reembolso), com regra de carência configurável como dado; suspensão e reativação do cliente sem apagar dado;
pausa segura de envios e do vendedor IA quando suspenso; tela "Meu plano" (plano, uso de IA no mês, limite do
número, estado, próxima cobrança quando a plataforma informar); trilha de auditoria; D5 (reembolso segue a
regra da plataforma) respeitada. Cobrir idempotência (evento repetido, fora de ordem) e isolamento por
cliente. Fora: cobrança própria, cartão, nota fiscal.

### Bloco 13: Relatórios de recuperação e retorno (ROI)

Objetivo: o cliente enxergar quanto o AtendeVendeIA recuperou. Entregar: consultas e tela de relatório com
funil (casos abertos, mensagens enviadas, respostas, vendas recuperadas, valor recuperado), por período, canal,
produto e sequência; atribuição exatamente pela regra decidida (D6: 5 dias, último toque por mensagem
enviada, mesmo produto), sem reinterpretar; exportação em CSV segura contra injeção de fórmula; números só do
próprio cliente (RLS); valores em centavos e moeda tratados pelo módulo de dinheiro existente. Documentar
que os números são do sistema e não medidos em produção, e não usar percentuais de mercado em texto de venda.

### Bloco 14: Captura segura de eventos reais (Cakto e Hotmart)

Objetivo: destravar a maior incerteza do projeto no dia em que houver conta. Entregar: modo de captura
(desligado por padrão) que guarda, cifrado e com prazo de expiração, o corpo e os cabeçalhos relevantes de
eventos de checkout reais, com mascaramento de dados pessoais ao exibir; comando de CLI e tela de
administração que comparam o evento capturado com o normalizador e dizem campo a campo o que bateu, o que
faltou e o que sobrou; testes com payloads sintéticos; fixtures prontas para entrar assim que um evento real
for colado. Hotmart: se o Fábio colar a documentação ou um evento no chat, ajuste o normalizador conforme
ela e marque o que passou a estar confirmado; senão, deixe tudo como suposição documentada. Nunca registrar
segredo, hottok, token nem dado pessoal em log.

### Bloco 15: Prontidão de subida ao servidor (sem subir)

Objetivo: no dia de subir, tudo ser rápido e seguro. Entregar: comando `preflight` que confere configuração
de staging/produção (variáveis obrigatórias, `FM_ENV`, verificador simulado recusado, chave de cifragem no
formato, papéis do banco, migrations, `FM_WHATSAPP_LIVE`, canais) e termina com 0/1/2; `smoke` que, dada uma
URL, confere `/v1/health`, `/v1/ready`, login, cabeçalhos e que o painel repassa `/v1`; roteiro de subida
e de rollback ensaiado em Postgres local (com o `backup.sh`/`restore_check.sh`); revisão do `render.yaml` e
de `docs/render.pago.yaml` contra o código (nomes de variáveis, worker, cron `ops-check`); atualização de
`docs/STAGING_RENDER.md` e `docs/OPERACAO.md`; checklist final da seção 5 de `docs/LANCAMENTO_MVP.md` com o
que ainda depende de conta real. **Não suba nada.**

## CRITÉRIO DE CONCLUSÃO

O trabalho termina quando os blocos 11 a 15 estiverem **mergeados no `main` com o CI do `main` verde** e a
documentação atualizada, ou quando restar apenas o que depende do Fábio ou de conta real (já registrado em
`docs/PENDENCIAS_EXTERNAS.md`). Não pare no meio por cansaço, dúvida pequena ou erro de CI: decida com o
bom senso do repositório, corrija e continue. Pare e pergunte ao Fábio **apenas** se: uma decisão de produto
ou de preço for necessária e não existir no checklist; houver risco a dado ou segurança; ou algo exigir
gasto, conta real ou outro repositório.

## RELATÓRIO FINAL (ao terminar)

Entregue ao Fábio, em português claro: o que cada bloco entregou, link de cada PR e commit de merge, estado
do CI do `main`, o que foi verificado e como, o que **não** está confirmado, pendências que dependem dele
(contas, decisões, documentação da Hotmart), e o próximo passo recomendado. Atualize `docs/CONTINUIDADE.md`
e avise que a cópia no Google Drive ("AtendeVendeIA (cópia do projeto)") precisa ser refeita.
