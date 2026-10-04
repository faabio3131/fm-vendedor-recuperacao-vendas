# Pendências externas (contas e configurações fora do código)

Tudo aqui depende de conta, chave ou decisão fora do repositório. **A construção não espera por isto:** o
código roda contra simuladores. Cada item diz o que ele destrava e como validar quando existir.
Status: `PENDENTE` · `FEITO` (com data). Quem muda o status é o Fábio ou o Engenheiro Sênior após validar.

Regra: nenhuma chave entra no repositório. Valores vão para variáveis de ambiente (Render) ou para a central
de conexões de cada cliente.

## Plataforma (F&M)

| # | Item | Status | Destrava | Como validar |
|---|---|---|---|---|
| P1 | Google Cloud: tela de consentimento + OAuth Client ID (web). Variável `FM_GOOGLE_CLIENT_ID` e `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | PENDENTE | Login real (hoje só simulado em dev) | Entrar com conta Google em staging; e-mail não verificado deve ser recusado |
| P2 | Chave do Gemini (`FM_AI_API_KEY`, API e worker) e modelo (`FM_AI_MODEL`) | PENDENTE | Vendedor IA real | `python -m fm_seller.cli ai-check` termina com `RESULTADO: OK`; conferir tokens por resposta (o `ai-check` imprime) e o preço em `docs/LANCAMENTO_MVP.md` §2.4 (sobe em 01/01/2027) |
| P3 | Hospedagem (Render): API, worker, painel, Postgres gerenciado, domínio e HTTPS (`render.yaml` é rascunho) | PENDENTE | Ambiente de staging/produção | `/health` ok, migrations aplicadas, `FM_ENV=staging` sobe sem erro de configuração |
| P4 | Papéis do Postgres de produção (`fm_owner`, `fm_app`) e chave de cifragem (`gen-key`) guardada em cofre, com cópia de segurança separada | PENDENTE | Credenciais de clientes seguras | Perder a chave perde as credenciais: testar restauração antes de ter cliente |
| P5 | Backups do banco e alertas (webhook parado, fila, falha de envio) | PENDENTE | Operar com clientes | Restaurar um backup em banco de teste |
| P6 | Cakto: cadastrar o produto SaaS (assinatura), segredo do webhook (`FM_PLATFORM_CAKTO_SECRET`), `map-product` | PENDENTE | Venda automática do SaaS na Cakto | Compra de teste cria cliente, plano e convite; conferir se a assinatura `X-Cakto-Signature` valida com o evento real (formato já alinhado à documentação) |
| P7 | Hotmart: idem (`FM_PLATFORM_HOTMART_HOTTOK`) | PENDENTE | Venda automática do SaaS na Hotmart | Idem; a documentação oficial da Hotmart não pôde ser lida: colar a página de webhook ou um evento real e **confirmar cabeçalho, nomes de evento e campos** (hoje são suposições) |
| P8 | Termos de uso, política de privacidade e papel de operador de dados (LGPD) em texto jurídico | PENDENTE | Vender legalmente | Revisão de advogado; link no login e na área de conexões |
| P9 | Contador: obrigações do Fábio como pessoa física com as vendas do SaaS; quando abrir CNPJ | PENDENTE | Segurança fiscal | Orientação por escrito |
| P10 | CNPJ + verificação de empresa na Meta | PENDENTE (não bloqueia) | Futuro: Tech Provider / Embedded Signup | Só necessário para conectar o WhatsApp do cliente com poucos cliques |
| P11 | `FM_WHATSAPP_LIVE=true` (API e worker) e versão da Graph API (`FM_META_GRAPH_VERSION`) | PENDENTE | Envio real, teste real da conexão e templates na Meta | Só depois dos testes 1 a 3 abaixo, com conta real |

## Por cliente (cada cliente configura o dele, sem mudar código)

| # | Item | Status | Observação |
|---|---|---|---|
| C1 | Conta Meta Business, app e WhatsApp Business Account com número próprio | PENDENTE por cliente | Número já em uso no app WhatsApp Business pode exigir coexistência/migração: **a verificar** |
| C2 | Cadastrar na central de conexões: ID do número, ID da conta, token, segredo do app; colar a URL de callback e o token de verificação na Meta | PENDENTE por cliente | Segredo do app é obrigatório (assinatura dos webhooks) |
| C3 | Templates (`carrinho_*`, `pix_*`, `boleto_*`, `recusada_*`, `orcamento_*`, `conversa_*`) enviados para aprovação pelo painel e aprovados pela Meta | PENDENTE por cliente | Envio e sincronização existem, mas só contra servidor falso; precisa de `FM_WHATSAPP_LIVE` |
| C4 | Declarar consentimento dos contatos e ligar a recuperação | PENDENTE por cliente | Feito no painel, em Recuperação → Ajustes |
| C5 | Cadastrar ofertas (preço e link https) | PENDENTE por cliente | Sem ofertas ativas o vendedor IA passa a conversa para uma pessoa |
| C6 | Messenger e Instagram: página do Facebook e conta profissional do Instagram ligadas ao app Meta; ID, token de acesso e segredo do app cadastrados na central de conexões; URL de callback e token de verificação colados na Meta | PENDENTE por cliente | Permissões de mensagens e a revisão do app pela Meta podem ser exigidas fora do modo de teste: **a verificar**. Envio real exige `FM_WHATSAPP_LIVE` |

## Testes que só podem ser feitos com as contas

1. WhatsApp real: handshake do webhook, assinatura, mensagem recebida, resposta, status de entrega, "SAIR"; **teste da conexão** (token, número, conta) e **envio de texto e de template** (formato do corpo conferido contra a resposta real).
2. Template real: criar pela API (`POST /<WABA>/message_templates`), conferir aprovação/recusa e motivo na sincronização, categoria devolvida, variável no início/fim do texto, e envio fora da janela de 24 h com parâmetros.
3. Compra de teste Cakto e Hotmart (plataforma) e eventos de checkout (clientes que usam essas plataformas).
4. Gemini real: `ai-check` e uma conversa de ponta a ponta.
5. Login Google real em staging.
6. Messenger e Instagram reais: handshake, assinatura, mensagem recebida (ID de quem escreve), resposta (formato de envio e janela de 24 h), entrega/leitura e "SAIR". O formato do envio do Instagram (qual ID e qual endereço da Graph API) **não foi confirmado**.
7. Limites da Meta: camada de mensagens do número (250 → 2.000 contatos únicos por 24 h); ajustar `Limite diário do número` em Recuperação.

## Decisões de produto abertas

- Janela de atribuição de venda recuperada: decidida em 5 dias (D6, 04/10/2026).
- Limites e preços por plano (valores atuais de `plans.limits`, 1000 respostas de IA por mês, são **provisórios**; custo real por resposta só se sabe após o `ai-check`).
- Sequências e textos padrão da recuperação.
- Quando buscar Tech Provider (exige CNPJ).
