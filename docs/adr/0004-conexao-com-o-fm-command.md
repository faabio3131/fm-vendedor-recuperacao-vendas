# ADR-0004 — Conexão com o FM Command: leitura agregada por token de serviço

Status: ADOTADA no trabalho de integração com o FM Command (05/10/2026); aguarda ciência do Diretor · Data: 2026-10-05

## Contexto

O Fábio opera a F&M Tecnologia e seus SaaS por um centro de controle próprio, o **FM Command** (repositório `FM-CONTROL-CENTER`, nome técnico
FMCC). O FM Command integra cada produto por HTTP governado, pelo seu Integration Fabric: um conector faz `health` e `pull` de um *snapshot*
com fatos canônicos, usando um token de serviço guardado por referência; o FM Command **nunca** lê o banco do produto (é a regra dele para o
Kordena, `FMCC-KORDENA-KCA12-INTEGRATION-v0.1.md`). O AtendeVendeIA precisa expor o mesmo tipo de superfície.

## Decisão

Três rotas só de leitura, em `/v1/control-plane/fmcc/` (`health` e `snapshot`), com estas cercas:

1. **Desligada por padrão.** Sem `FM_FMCC_CONTROL_PLANE_TOKEN` (mínimo 32 caracteres, validado na configuração) as rotas respondem 404.
2. **Token de serviço**, `Authorization: Bearer`, comparado em tempo constante (digests de tamanho fixo). Sessão de pessoa não vale; token vazio, curto
   ou errado dá 401. Falhas repetidas travam o IP (mesma trava dos webhooks recusados, `FM_RATE_WEBHOOK_FAIL_PER_10MIN`).
3. **Só agregados e fatos opacos.** Contagens e fatos de assinatura (`subscription.active`, `subscription.cancelled`) com identificador do tipo
   `sub:<cliente>:<estado>`. **Nunca** conversa, contato, telefone, e-mail, nome de cliente, credencial nem texto livre; testes derrubam o build se
   qualquer um desses aparecer.
4. **O que não existe não vira zero.** O mapa `coverage` declara `available`, `partial` ou `unavailable` por domínio (cobrança, pagamentos, custos,
   leads e suporte são `unavailable`: a cobrança é da Cakto/Hotmart, não deste sistema). Faltou fonte, o FM Command mostra "indisponível".
5. **Sem escrita.** Nenhum comando do FM Command altera o AtendeVendeIA por aqui (suspender, trocar plano etc. continuam na área `/admin`).

## Exceção à regra 4 do AGENTS.md

A leitura agrega **todos os clientes**, portanto roda em modo `system` do banco, fora de rota de pessoa ou de cliente (como o `/v1/admin`, ADR-0003).
As consultas são fixas, sem SQL montado de entrada, e só devolvem colunas escolhidas à mão. A superfície é pequena para ser revisável.

## Consequências

- O lado do FM Command (o conector `atendevendeia`) **não existe ainda** e é trabalho no repositório dele, por decisão do Fábio. O contrato
  completo e o que o FM Command precisa implementar estão em `docs/FM_COMMAND_INTEGRACAO.md`.
- Nada foi testado contra o FM Command de verdade, só contra o contrato descrito. Primeira prova real: o `health` e o `snapshot` chamados pelo conector.
- Um erro numa consulta de `control_plane.py` seria vazamento entre clientes: qualquer coluna nova passa por revisão e pelo teste de privacidade.
- Para desligar tudo: apagar a variável. Para trocar o token: mudar nos dois lados e reiniciar a API.
