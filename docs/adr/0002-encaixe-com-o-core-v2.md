# ADR-0002 — MVP agora, encaixe com o FM Core Base e o FM Assistant Base na V2

Status: PROPOSTA (aguarda confirmação do Diretor) · Data: 2026-10-03

## Contexto

- Decisão do Diretor (03/10/2026): lançar rápido um produto mínimo viável com a forma de construção
  atual e, depois, uma V2 sobre o Core, quando ele estiver pronto como base fundamental.
- O **FM Core Base** deve ser a base de todos os SaaS da F&M (inteligência cognitiva vertical, não um
  chatbot). O **FM Assistant Base** é a camada de atendimento acoplada a ele, também com inteligência
  vertical. Ambos nasceram como cópias isoladas do sistema de restaurante (`fm-ai-platform`, commit
  `5a17b0c`) e ainda serão transformados em produtos.
- Leitura feita em 03/10/2026 (somente leitura): o Core roda sozinho (55 testes passam; um arquivo de teste
  depende do módulo `core.runtime.backup`, que não veio na cópia). O Assistant **não roda sozinho**: depende
  de 48 módulos que ficaram na origem (pedido, estoque, entrega, pagamentos, CRM do restaurante). O Core
  isola cliente na aplicação (SQLAlchemy, aceita SQLite), sem RLS; segredos por referência de ambiente; o
  roteador exige `tenant_id` **e** `unidade_id`; só há adaptador real para Gemini.
- Este produto tem o que o Core e o Assistant não têm: recuperação (sequências, casos, conversa que esfriou,
  planilha), templates da Meta e janela de 24 h, opt-out, limites por plano, login Google, painel, RLS.

## Decisão

1. **O MVP segue como está.** Nada do Core entra antes do lançamento.
2. **Direção de dependência (V2):** este produto depende do Assistant, que depende do Core. Nunca o
   contrário, e o Core não conhece este produto.
3. **Quem é dono do quê na V2:**
   - Este produto: canal (WhatsApp/Meta), conformidade de envio (template aprovado, janela de 24 h, opt-out,
     horário de silêncio, limites), recuperação, planos, tenancy e credenciais.
   - Assistant/Core: a cognição (entender a conversa, decidir a próxima ação, roteamento entre modelos,
     custo/FinOps, transcrição de áudio).
   - Regra mantida: **a IA propõe, o sistema decide.** Preço, desconto, prazo e link vêm do cadastro do cliente.
4. **Pontos de encaixe já existentes (não criar outros):**
   - `ai.model.AiModel` (`AiContext` → `AiReply`): a V2 troca o adaptador por um que chama o Assistant.
   - `ai.usage` e `plans.limits`: a medição por contagem e tokens passa a poder usar o evento de uso e o
     custo do Core, sem mudar a regra de limite do plano.
   - `MessageSender`, `TemplateGateway`, `ConnectionTester`: ficam no produto.
5. **Persistência na V2:** o Core/Assistant devem ser usados como biblioteca com **portas** de persistência
   que o produto implementa sobre o seu Postgres com RLS. O Core não traz banco próprio para dados de cliente.
6. **Congelar cognição aqui:** enquanto o Assistant não está pronto, não construir no produto novas
   capacidades cognitivas além do necessário para o MVP (evita duas versões paralelas).

## Pontos em aberto para a V2

- `unidade_id` é obrigatório no roteador do Core. Este produto não tem unidade: mapear (ex.: uma unidade
  padrão por cliente) ou tornar o campo opcional no Core.
- O Assistant foi escrito para pedido/cardápio/entrega. É preciso decidir como oferta e orçamento do
  comércio local entram como capacidade de domínio (porta), não como código do restaurante.
- Troca de adaptador exige reexecutar o `ai-check` e rever custo por resposta e limites por plano.

## Critérios para considerar o Core "pronto" (proposta, o Diretor define)

1. Testes do Core e do Assistant passando sozinhos (sem módulos faltando) e rodando no CI.
2. Teste de arquitetura impedindo importação de qualquer produto vertical.
3. Pacote instalável e versionado, com contrato de portas documentado.
4. Isolamento por cliente com garantia equivalente à nossa (ou uso apenas atrás das portas do produto).
5. Adaptadores de modelo reais além do Gemini, ou decisão explícita de ficar só com ele.
6. Atendimento sem dependência de domínio de restaurante (pedido, estoque, entrega, KDS).

## Consequências

- Lançamento não depende do Core. O custo é uma troca de adaptador na V2, pequena porque a porta já isola.
- Vender o vendedor IA do MVP como "básico" até a V2; a recuperação funciona sem cognição.
- Risco: se o Core mudar de contrato, o adaptador da V2 muda junto. Mitigação: versionar o contrato.
