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
7. **Provedores de canais governados.** WhatsApp pode usar **Meta Cloud API oficial ou Evolution API**
   por adaptadores próprios na mesma aplicação (ADR-0006); Messenger e Instagram continuam via APIs
   oficiais da Meta. Não misturar as identidades, credenciais ou webhooks dos provedores, nem criar
   um segundo motor de conversas/recuperação, persistência ou autenticação. **Evolution autorizada
   arquiteturalmente não significa envio habilitado.** Para **AtendeVendeIA**, o Diretor escolheu
   explicitamente **Evolution API + WhatsApp Web/Baileys por QR Code** em 20/10/2026;
   **não confundir com VendeAtendeIA**. A escolha da modalidade não dispensa avaliação dos
   termos e riscos de bloqueio/desconexão, nem autoriza ativação comercial sem certificação.
   Exigir vínculo por tenant e instância, sessão recuperável, conexão verificada, autenticação
   dos webhooks, consentimento, opt-out, idempotência, auditoria, testes reais e revogação;
   em falhas de autenticação ou escopo, negar por padrão.
8. **Preços de provedores são dado versionado com data**, não constante no código.
9. **Evidência antes de "concluído".** Ruff, mypy strict e pytest passando, CI verde, riscos
   residuais registrados. Teste simulado não prova integração real.
10. Nenhum merge, deploy ou promoção para produção é automático; exigem aprovação do Fábio.

## Escopo do trabalho

Este repositório é o único autorizado para o agente. Alterar outros repositórios exige autorização
expressa do Fábio.
