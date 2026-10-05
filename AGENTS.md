# AGENTS.md — fm-vendedor-recuperacao-vendas

Produto comercial da F&M Tecnologia. Autoridade final: Fábio (Diretor Executivo).

## Regras obrigatórias

1. **Web First, uma só construção.** Nada de versão local ou mobile para migrar depois
   (política "FM Technology Web-First SaaS Engineering Policy"). Mudança aqui exige ADR.
2. **Nada de segredo no repositório**: tokens, chaves, senhas, certificados, dados reais de cliente.
   Exemplos e testes usam dados sintéticos. `.env` fica fora do git.
3. **Cliente novo não muda código.** Credenciais, ofertas, mensagens e horários são dados
   configurados pelo cliente na central de conexões e no painel.
4. **Isolamento por cliente em toda tabela com dado de cliente**: `tenant_id`, RLS forçada, teste de
   isolamento. O app conecta com o papel `fm_app` (sem bypass). Modo `system` só para rotinas
   de plataforma, nunca em rota de usuário.
5. **A IA propõe, o sistema decide.** Preço, desconto, prazo e link de pagamento vêm do cadastro do
   cliente, nunca do texto do modelo. A IA nunca recebe token ou credencial.
6. **Nunca declarar sucesso sem verificação.** Conexão só vira "conectada" depois de teste real;
   sem adaptador real, o resultado é "não confirmada". Estado de envio vem do provedor, não da intenção.
7. **Canais Meta oficiais apenas** (WhatsApp Cloud API, Messenger, Instagram). Nada de WhatsApp via QR code.
   Consentimento e opt-out são respeitados em todo envio.
8. **Preços de provedores são dado versionado com data**, não constante no código.
9. **Evidência antes de "concluído".** Ruff, mypy strict e pytest passando, CI verde, riscos
   residuais registrados. Teste simulado não prova integração real.
10. Nenhum merge, deploy ou promoção para produção é automático; exigem aprovação do Fábio.

## Escopo do trabalho

Este repositório é o único autorizado para o agente. Alterar outros repositórios exige autorização
expressa do Fábio.

## Execução de plano mestre

Vale para qualquer executor (Claude, ChatGPT/Codex, outra IA ou pessoa). O plano fica no repositório (`docs/plano/` ou
`docs/CRONOGRAMA_MESTRE.md`), no formato de `docs/MODELO_PLANO_MESTRE.md`. A conversa **não** é a fonte do plano.

1. **Leia o plano inteiro, de ponta a ponta, antes de começar**, junto de `AGENTS.md`, `README.md` e `docs/CONTINUIDADE.md`.
   Plano com estado diferente de `APROVADO` ou `EM EXECUÇÃO` não é executado.
2. **Siga a ordem.** Um item por vez; não pule, não reordene, não funda nem invente itens. Dúvida ou conflito entre o plano e estas regras:
   pare e pergunte ao Fábio, não decida sozinho.
3. **Item só é concluído com o critério de aceite cumprido e provado** (comandos rodados, saída conferida). Marque `[x]` e registre
   a PR, o merge e o que foi verificado **depois** de verificar. Nunca marque antes, nunca marque por suposição.
4. **Uma PR por bloco ou item**, só com o escopo dele. Merge só com os três checks do CI (api, web, e2e) verdes no commit final,
   sem conflito, e dentro da autorização escrita no plano (regra 10). Falha de CI: corrija a causa; não pule, não enfraqueça
   nem apague teste, não faça commit vazio, não feche e reabra a PR.
5. **Confirme o CI do `main` verde antes de começar o item seguinte.**
6. **Não invente.** O que o plano não diz e o repositório não mostra vira "não confirmado" e vai ao relatório. Decisão de produto, preço,
   texto jurídico, segurança, dado de cliente, gasto, conta real ou outro repositório: pare e pergunte, ou use um padrão provisório
   configurável e registre como pendência do Fábio.
7. **Plano muito grande não cabe na memória: releia o item antes de executá-lo** e confira o registro de execução no arquivo,
   não no que lembra. Se perdeu o fio, recomece pela leitura do plano e do registro.
8. **Ao concluir:** relatório final no formato do modelo (entregas, PRs e merges, CI do `main`, verificado, não confirmado,
   pendências do Fábio, próximo passo) e atualização de `docs/CONTINUIDADE.md`.
