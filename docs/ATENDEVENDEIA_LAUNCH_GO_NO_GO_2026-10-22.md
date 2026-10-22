# AtendeVendeIA — Commercial Launch V1 (22/10/2026)

## Decisão executiva
Prioridade de lançamento: operar com administração local existente, isolando Command
do caminho crítico. A futura integração Command deve ser migração governada da autoridade
comercial, não criação de segunda cobrança, licença ou persistência.

## CURRENT confirmado
- GitHub main de partida: 61f6a75b0ca50226f9cd3835ddfdfe73127d8cbc.
- Web/API/Worker na Contabo e Evolution 2.3.7 integrados; WhatsApp piloto já pareado.
- Na última consulta read-only: evolution_connected=1, ai_enabled_for_evolution=0,
  active_offers_for_evolution=0, inbound_messages_for_evolution=0.
- Franquia já é dado em `plans.limits.ai_replies_per_month`; o vendedor bloqueia
  ao chegar ao limite e a interface Meu plano mostra consumo.
- As quatro fases históricas são preservadas; não são catálogo comercial V1.
- Links de checkout reais e compra validada ainda **não certificados**.

## TARGET — oferta de lançamento aprovada como instrução do Diretor
| Plano | Preço mensal (BRL) | Interações processadas por IA/mês |
|---|---:|---:|
| av-essencial | 129,90 | 500 |
| av-profissional | 199,90 | 5.000 |
| av-escala | 399,90 | 15.000 |

Mesma matriz de funcionalidades efetivamente implementadas e homologáveis entre os
três planos, sem prometer módulos futuros. A definição de "interação IA" é chamada
bem-sucedida ao modelo, inclusive quando o modelo decide fazer handoff (custo existe).
Falhas de modelo não consomem franquia. Mensagens recebidas e atendimento humano
continuam acessíveis após o limite.

## Bloco de construção desta PR
- Migração aditiva: catálogo V1 e coluna de preço mensal; nenhuma alteração nas
  assinaturas antigas e nenhuma cobrança automaticamente disparada.
- Administração local restrita a platform_admin: leitura e edição de preço/franquia,
  histórico auditável; nenhuma edição de plano por cliente.
- Reforço do bloqueio: trava transacional de assinatura por tenant antes de chamar
  modelo e registrar consumo; limite ausente/inválido bloqueia (fail closed).
- Testes: preço, franquia, permissões, idempotência de atualização, continuidade do
  plano histórico e bloqueio.

## Gates de GO/NO-GO — checklist obrigatório
- [ ] CI completa para HEAD exato: API, Web, E2E, deploy config.
- [ ] PostgreSQL: migração 0015 em cópia/staging, backup verificado, execução
      produtiva somente com autorização e janela/rollback.
- [ ] Simultaneidade: teste concorrente real no PostgreSQL comprova nunca ultrapassar
      a franquia; teste de virada de mês no fuso do tenant.
- [ ] Evolution: webhook autenticado e anti-replay com mensagem recebida no tenant,
      envio real/IA habilitado controladamente, resposta entregue e auditada.
- [ ] IA: chave/modelo real e teste do vendedor, custo e tokens medidos; texto/valor
      não inventados; nome da assistente configurado.
- [ ] Oferta fictícia desativada; dados do produto próprios e URLs oficiais válidos.
- [ ] Checkout real para 3 planos, provider/KYC/regulatório em conformidade, sem
      reutilizar URLs fictícias; teste de compra, confirmação, licença e acesso.
- [ ] Cancelamento/atraso/renovação, upgrade/downgrade e estorno governados e
      testados antes de anunciá-los como disponíveis.
- [ ] Health, worker, backup/rollback e testes de acesso entre tenants verdes.
- [ ] Aprovação humana GO explícita; só então alterar site de "Em breve" para venda.

**Não declarar lançamento comercial apenas por build ou CI verde.**
