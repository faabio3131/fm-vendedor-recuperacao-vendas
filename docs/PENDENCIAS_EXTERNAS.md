# Pendências externas (contas e configurações fora do código)

Tudo aqui depende de conta, chave ou decisão fora do repositório. **A construção não espera por isto:** o
código roda contra simuladores. Cada item diz o que ele destrava e como validar quando existir.
Status: `PENDENTE` · `FEITO` (com data). Quem muda o status é o Fábio ou o Engenheiro Sênior após validar.

Regra: nenhuma chave entra no repositório. Valores vão para variáveis de ambiente (Render) ou para a central
de conexões de cada cliente.

## Plataforma (F&M)

| # | Item | Status | Destrava | Como validar |
|---|---|---|---|---|
| P1 | Google Cloud: tela de consentimento + OAuth Client ID (web). Variável `FM_GOOGLE_CLIENT_ID` e `NEXT_PUBLIC_GOOGLE_CLIENT_ID`. **Staging FEITO** (projeto `atendevendeia-staging`, cliente OAuth web com origem `https://fm-seller-web.onrender.com`, app em "Testando" com o Gmail do Diretor como usuário de teste; login real testado em 07/10/2026, ver `docs/TESTES_REAIS_REGISTRO.md`). **Produção (quando a hospedagem existir):** (1) acrescentar a origem do painel de produção no mesmo cliente; (2) em Branding, links da Política de Privacidade e dos Termos publicados; (3) mudar o app de "Testando" para "Em produção" (sem isso só entram os e-mails de teste, até 100; só e-mail e perfil, sem escopo sensível, a confirmar na tela) | STAGING FEITO; produção PENDENTE | Login real de clientes | Entrar com um Gmail que não está na lista de teste; e-mail não verificado deve ser recusado |
| P2 | Chave do Gemini (`FM_AI_API_KEY`, API e worker) e modelo (`FM_AI_MODEL`). **Decisão de 08/10/2026: manter `gemini-3.1-flash-lite` (`FM_AI_MODEL` no Render) até a produção avançada**; reavaliar o `gemini-3.8-flash` com a cota ampliada (4 tentativas deram 429/503) antes de 31/12/2026 | PARCIAL | Vendedor IA real | `python -m fm_seller.cli ai-check` termina com `RESULTADO: OK` e `ai-eval --real` sem falha de segurança; conferir tokens por resposta (o `ai-check` imprime) e o preço em `docs/LANCAMENTO_MVP.md` §2.4 (sobe em 01/01/2027) |
| P3 | Hospedagem: **staging** no Render grátis (decisão de 05/10/2026; sem worker, ver `docs/STAGING_RENDER.md`) e **produção** em provedor no exterior (decisão de 08/10/2026; provável Render, ainda não fechado). **Alternativa mais barata preparada: um servidor Hetzner, arquivos e passo a passo em `docs/HETZNER_PRODUCAO.md` (backup e restauração testados; compose, HTTPS e deploy ainda não executados)**: API, worker, painel, Postgres gerenciado, domínio e HTTPS (`render.yaml` é rascunho; **suspenso de propósito até o item B1 do `docs/PLANO_FASE_STAGING.md`**) | PENDENTE | Ambiente de staging/produção | `cli preflight` e `cli smoke` sem crítico, `/health` ok, migrations aplicadas, `FM_ENV=staging` sobe sem erro de configuração |
| P4 | Papéis do Postgres de produção (`fm_owner`, `fm_app`) e chave de cifragem (`gen-key`) guardada em cofre, com cópia de segurança separada | PENDENTE | Credenciais de clientes seguras | Perder a chave perde as credenciais: testar restauração antes de ter cliente |
| P5 | Backups do banco e alertas (webhook parado, fila, falha de envio) | PENDENTE | Operar com clientes | Restaurar um backup em banco de teste |
| P6 | Cakto: cadastrar o produto SaaS (assinatura), segredo do webhook (`FM_PLATFORM_CAKTO_SECRET`), `map-product` | PENDENTE | Venda automática do SaaS na Cakto | Compra de teste cria cliente, plano e convite; conferir se a assinatura `X-Cakto-Signature` valida com o evento real (formato já alinhado à documentação); capturar um ciclo real de assinatura (atraso, recuperação, cancelamento, reembolso) e conferir nomes e ordem dos eventos |
| P7 | Hotmart: idem (`FM_PLATFORM_HOTMART_HOTTOK`) | PENDENTE | Venda automática do SaaS na Hotmart | Idem; a documentação oficial da Hotmart não pôde ser lida: colar a página de webhook ou um evento real e **confirmar cabeçalho, nomes de evento e campos** (hoje são suposições) |
| P8 | Termos de uso, política de privacidade e papel de operador de dados (LGPD). **Termos e Política de Privacidade: aprovados pelo Diretor e publicados no site (05/10/2026; atualizados em 08/10/2026 para hospedagem no exterior e voz). Por decisão do Diretor, sem revisão de advogado** (a transferência internacional do art. 33 da LGPD consta). **Acordo de Operador de Dados: pontos em aberto preenchidos e aprovados pelo Diretor em 09/10/2026 (aviso de novo suboperador 15 dias; auditoria 1 vez por ano; teste de invasão: constar que não houve); publicado no site em `/acordo/` (09/10/2026), com link no rodapé e nos Termos (aceite pelo contrato dos Termos, sem caixa de aceite no painel)** | TERMOS E PRIVACIDADE PUBLICADOS; TERMOS, PRIVACIDADE E ACORDO PUBLICADOS | Vender legalmente | Link dos documentos no login e na área de conexões. A tela Privacidade já tem o espaço (hoje diz "PENDENTE"); exportar, apagar, retenção e exclusão da conta já existem e foram testados só com dados sintéticos |
| P9 | Contador: obrigações do Fábio como pessoa física com as vendas do SaaS; quando abrir CNPJ | PENDENTE | Segurança fiscal | Orientação por escrito |
| P10 | CNPJ + verificação de empresa na Meta | PENDENTE (não bloqueia) | Futuro: Tech Provider / Embedded Signup | Só necessário para conectar o WhatsApp do cliente com poucos cliques |
| P12 | Conexão com o FM Command: gerar `FM_FMCC_CONTROL_PLANE_TOKEN` (mín. 32 caracteres, cofre) na API do AtendeVendeIA e implementar o conector `atendevendeia-v1` no repositório do FM Command (`docs/FM_COMMAND_INTEGRACAO.md`) | PENDENTE | Ver o AtendeVendeIA no centro de controle (assinaturas, saúde, indisponível onde não há fonte) | `health` verde e `snapshot` com fatos puxados pelo conector do FM Command, com os dois lados no ar em HTTPS; só então marcar como conectado |
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
3. Compra de teste Cakto e Hotmart (plataforma) e eventos de checkout (clientes que usam essas plataformas), **com a captura ligada** (`docs/OPERACAO.md`): `capture show` diz o que bateu e o que faltou, e `capture export` gera a fixture anonimizada para o repositório.
4. Gemini real: `ai-check` e uma conversa de ponta a ponta.
5. Login Google real em staging.
6. Messenger e Instagram reais: handshake, assinatura, mensagem recebida (ID de quem escreve), resposta (formato de envio e janela de 24 h), entrega/leitura e "SAIR". O formato do envio do Instagram (qual ID e qual endereço da Graph API) **não foi confirmado**.
7. Limites da Meta: camada de mensagens do número (250 → 2.000 contatos únicos por 24 h); ajustar `Limite diário do número` em Recuperação.

## Operação: o que só o ambiente real responde

Criar o primeiro administrador da plataforma no staging (`cli create-platform-admin --email ...`) e entrar com o Google de verdade;
conferir a tela de administração com clientes reais de teste; rodar o `loadtest` contra o staging **só com `--remote` e sabendo o
custo** (os números do repositório são da máquina de teste); decidir se e como a F&M quer ser avisada de incidente (hoje só `ops-check`
por cron e a tela de saúde: não há e-mail, WhatsApp nem serviço externo, ficou fora de escopo).

## Segurança: o que só o ambiente real responde

Ver o fim de `docs/SEGURANCA.md`. Resumo: conferir no staging qual IP chega à API atrás do painel (define `FM_TRUST_PROXY`), que o
proxy do Render não remove os cabeçalhos de segurança (`cli smoke` avisa), rodar o `preflight`, e contratar um teste de invasão
antes de abrir para clientes.

## Decisões de produto abertas

- Janela de atribuição de venda recuperada: decidida em 5 dias (D6, 04/10/2026).
- Carência em atraso antes de suspender (D10): **3 dias, decidido em 05/10/2026**. Carência da exclusão da conta: **30 dias, decidido em 05/10/2026**.
- Segurança (Bloco 19), decisões do Diretor: duração da sessão e expiração por inatividade (hoje 14 dias fixos); nonce na CSP do painel; contratar teste de invasão.
- Privacidade (Bloco 18), decisões do Diretor e do advogado: prazo de retenção das conversas (365 dias provisório), carência da exclusão da conta (30 dias provisório), e se o bloqueio de contato deve ser guardado só como hash com chave própria (hoje fica o telefone ou ID, sem nome nem mensagens, para não correr o risco de esquecer o bloqueio na rotação da chave).
- Limites e preços por plano (valores atuais de `plans.limits`, 1000 respostas de IA por mês, são **provisórios**; custo real por resposta só se sabe após o `ai-check`).
- Sequências e textos padrão da recuperação.
- Quando buscar Tech Provider (exige CNPJ).
- Senha do dono do banco do staging (`neondb_owner`, Neon): foi exposta em conversa privada em 06/10/2026. Trocar ("Reset password" no Neon) ao fim do staging e atualizar `FM_DATABASE_ADMIN_URL` no Render.
- Segredo do cliente OAuth do Google (staging): apareceu em captura de tela enviada em conversa privada em 07/10/2026. O login não usa esse segredo; mesmo assim, criar um novo e apagar o antigo em Google Cloud, Clientes, no projeto `AtendeVendeIA staging`.
- IA multi-provedor: hoje só existe o adaptador da Gemini, na porta `AiModel` (`ai/model.py`). Falta escolher o provedor por configuração, adaptadores da Anthropic e da OpenAI, custo medido por provedor e comparação com `ai-eval`. Fábio pediu em 06/10/2026 que isso **não** entre agora: tratar depois do staging, em bloco próprio. Antes de decidir por preço, conferir a fonte oficial de preços.
  - Preços consultados em 07/10/2026 (por 1 milhão de tokens, entrada e saída): Gemini 3.8 Flash US$ 0,75 e 3,75 (preço promocional até 31/12/2026; de 01/01/2027 sobe para US$ 1,50 e 7,50); Gemini 3.1 Flash-Lite US$ 0,25 e 1,50; Gemini 3.5 Flash-Lite US$ 0,30 e 2,50; GPT-5.6 Luna US$ 0,20 e 1,20 (preço de terceiros: a página oficial da OpenAI bloqueou a consulta; conferir antes de decidir). Estimativa por 1.000 respostas (2.000 tokens de entrada e 300 de saída, suposição): 3.8 Flash cerca de US$ 2,60; Luna cerca de US$ 0,76.
  - **Não usar o Gemini 2.5 Flash-Lite**: desligamento anunciado para 16/10/2026 na API de desenvolvedor (20/10 na Vertex), com relatos de 404 desde julho.
  - Decisão por qualidade, não só preço: o `ai-eval` mede segurança e obediência (preço ou link inventado, opt-out, passar para uma pessoa), não poder de venda. Plano: rodar o `ai-eval` em 3.8 Flash, 3.1 Flash-Lite e Luna, e uma pessoa ler conversas lado a lado. Decidir antes de 31/12/2026.
