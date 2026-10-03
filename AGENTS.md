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
