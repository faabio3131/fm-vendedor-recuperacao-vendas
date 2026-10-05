# Política de Privacidade do AtendeVendeIA (RASCUNHO)

> **RASCUNHO para revisão de advogado (P8).** Itens entre `[colchetes]` a preencher. Versão do rascunho: 05/10/2026.
> Descreve o que o sistema **realmente faz hoje** (ver `docs/ARQUITETURA.md`, Bloco 18, e `docs/SEGURANCA.md`). Se o sistema mudar, atualize.

## 1. Papéis (LGPD, Lei 13.709/2018)

- **Cliente do AtendeVendeIA** (a loja ou empresa): **controlador** dos dados das pessoas que ele atende (contatos).
- **F&M Tecnologia** ([razão social], CPF/CNPJ [•]): **operador** desses dados, tratando-os só para prestar o Serviço e conforme as instruções do Cliente.
- Dos dados do **próprio Cliente** (nome, e-mail da conta, uso do painel), a F&M é **controladora**.
- Encarregado (DPO) / contato de privacidade: **[nome e e-mail]**.

## 2. Que dados tratamos

| Dados | De quem | Para quê | Base legal (a confirmar com advogado) |
|---|---|---|---|
| E-mail verificado e nome da conta Google, papel no painel | Cliente (usuário) | Login e controle de acesso | Execução de contrato |
| Telefone, nome, ID de canal (Messenger/Instagram), mensagens trocadas, dados do carrinho/pedido (produto, valor, link) | Contatos do Cliente | Atendimento e recuperação de vendas pedidos pelo Cliente | Definida pelo Cliente (consentimento ou legítimo interesse/execução de contrato) |
| Tokens e segredos de conexão (Meta etc.) | Cliente | Conectar canais | Execução de contrato |
| Registros de auditoria, consentimento declarado (quem, quando, origem), uso da IA (contagens) | Cliente e contatos | Segurança, prova de consentimento, limite de plano | Obrigação legal / legítimo interesse |
| Dados técnicos: IP, ID de requisição (em log, sem conteúdo de conversa) | Todos | Segurança e diagnóstico | Legítimo interesse |

**Não** tratamos dados sensíveis de propósito; oriente seus contatos a não enviá-los. **Não vendemos dados.**

## 3. Como protegemos

Isolamento por cliente no banco (RLS forçada), credenciais cifradas (AES-256-GCM), sessão por cookie `HttpOnly`, limites contra abuso, auditoria e
cabeçalhos de segurança. Detalhes técnicos em `docs/SEGURANCA.md`. Nenhum sistema é 100% seguro; em incidente com risco relevante, comunicaremos o Cliente e, quando exigido, a ANPD.

## 4. Com quem compartilhamos (suboperadores)

- **Meta** (WhatsApp, Messenger, Instagram): entrega das mensagens, sob os termos da Meta.
- **Google** (login) e **Google Gemini** (IA): o texto da conversa e o cadastro de ofertas são enviados ao modelo para gerar a resposta sugerida. **Credenciais nunca vão para a IA.**
- **Hospedagem e banco de dados**: **[provedor, país]**.
- Plataformas de venda (Cakto, Hotmart): recebemos delas os eventos de compra do Cliente.
- Autoridades, quando a lei exigir.

Transferência internacional: alguns suboperadores podem tratar dados fora do Brasil **[confirmar locais e salvaguardas]**.

## 5. Por quanto tempo guardamos

- **Conversas, casos encerrados, eventos brutos e contatos sem atividade:** apagados automaticamente após o prazo de retenção do Cliente (configurável de 30 a 3650 dias; padrão **365 dias** **[confirmar]**). Casos em andamento não são apagados.
- **Pedido de saída (opt-out):** guardamos só o identificador (telefone ou ID) e o motivo, **sem nome nem mensagens**, para **não voltar a contatar a pessoa**. É mantido enquanto o Cliente usar o Serviço.
- **Exclusão da conta:** após o pedido, **30 dias de carência** (dá para cancelar); depois, apagamos a conta, usuários exclusivos e eventos de compra, mantendo apenas o registro mínimo de que a exclusão ocorreu (id e datas).
- **Backups:** podem conter dados por **[•] dias** além disso, até a rotação do backup.
- **Auditoria e registros legais:** pelo prazo exigido em lei.

## 6. Direitos do titular (contato do cliente final)

Você pode pedir confirmação de tratamento, acesso, correção, anonimização/eliminação, portabilidade, informação sobre compartilhamento e revogação de consentimento (art. 18 da LGPD).
Como somos operadores, **peça primeiro à loja** que falou com você; ela usa o painel (menu Privacidade) para **baixar** ou **apagar** seus dados. Se não conseguir, escreva para **[e-mail do DPO]** e encaminharemos ao controlador.
Para parar de receber mensagens, responda **SAIR**.

## 7. Cookies

Usamos apenas o cookie de sessão, necessário para o login. Não usamos cookies de publicidade.

## 8. Crianças

O Serviço é para empresas e não se destina a menores de 18 anos.

## 9. Mudanças

Avisaremos mudanças relevantes pelo painel ou e-mail. Última atualização: **[data de publicação]**.
