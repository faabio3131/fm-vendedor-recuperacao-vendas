const DOCS = "https://developers.facebook.com/docs/whatsapp/cloud-api/get-started";

/** Passo a passo para quem conecta o próprio WhatsApp (sem CNPJ/Tech Provider: cada cliente usa o próprio app Meta). */
export function WhatsAppGuide() {
  return (
    <details className="guide">
      <summary>Como conectar o WhatsApp (passo a passo)</summary>
      <ol>
        <li>
          No Meta for Developers, abra ou crie um app do tipo Business. No painel do app, em WhatsApp, escolha “Set up”.
          Se você ainda não tem conta empresarial da Meta, ela é criada nesse passo.
        </li>
        <li>
          Em WhatsApp &gt; API Setup ficam o <strong>ID do número de telefone</strong> e o <strong>ID da conta do WhatsApp
          Business</strong>. Copie os dois para os campos abaixo.
        </li>
        <li>
          Para testar, a Meta oferece um número de teste e aceita até 5 destinatários confirmados por código. Para usar
          o número real do seu negócio, adicione-o na mesma tela. Um número já usado no aplicativo WhatsApp comum pode
          precisar de migração: confira na documentação da Meta antes.
        </li>
        <li>
          <strong>Token de acesso:</strong> o token temporário mostrado na tela serve só para testar e expira. Para uso
          real, gere um token permanente (em geral por um usuário do sistema no Gerenciador de Negócios, com as permissões
          de mensagens e de gerenciamento do WhatsApp Business). Confirme o procedimento na documentação.
        </li>
        <li>
          <strong>Segredo do app:</strong> fica em Configurações do app &gt; Básico. Sem ele, nenhuma mensagem recebida é
          aceita aqui.
        </li>
        <li>
          Preencha os campos abaixo e clique em <strong>Salvar</strong>. Copie a URL de callback e o token de verificação
          (ele aparece uma única vez) para a configuração de Webhooks do WhatsApp na Meta e assine o campo de mensagens.
        </li>
        <li>
          Clique em <strong>Testar conexão</strong>. Depois, em Recuperação &gt; Mensagens, envie os textos para aprovação
          da Meta: nada é enviado a clientes sem template aprovado.
        </li>
      </ol>
      <p className="muted">
        Os nomes dos menus da Meta mudam com o tempo. Documentação oficial:{" "}
        <a href={DOCS} target="_blank" rel="noopener noreferrer">
          WhatsApp Cloud API — primeiros passos
        </a>
        .
      </p>
    </details>
  );
}
