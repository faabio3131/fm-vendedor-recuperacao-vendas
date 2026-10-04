const DOCS = {
  messenger: "https://developers.facebook.com/docs/messenger-platform/",
  instagram_dm: "https://developers.facebook.com/docs/instagram-platform/",
} as const;

type Key = keyof typeof DOCS;

/** Passo a passo para conectar Messenger ou Instagram com o app Meta do próprio cliente. */
export function MetaChannelGuide({ provider }: { provider: Key }) {
  const messenger = provider === "messenger";
  const label = messenger ? "Messenger" : "Instagram";
  return (
    <details className="guide">
      <summary>Como conectar o {label} (passo a passo)</summary>
      <ol>
        <li>
          No Meta for Developers, use o mesmo app Business do WhatsApp ou crie um. Adicione o produto{" "}
          <strong>{messenger ? "Messenger" : "Instagram"}</strong> ao app.
        </li>
        <li>
          {messenger ? (
            <>
              Copie o <strong>ID da página</strong> do Facebook e gere o <strong>token de acesso da página</strong>. Use
              um token de longa duração: o token curto expira. Confirme o procedimento na documentação.
            </>
          ) : (
            <>
              A conta do Instagram precisa ser <strong>profissional</strong> e estar ligada ao app. Copie o{" "}
              <strong>ID da conta do Instagram</strong> e gere o <strong>token de acesso</strong> com a permissão de
              mensagens. Confirme o procedimento na documentação.
            </>
          )}
        </li>
        <li>
          <strong>Segredo do app:</strong> fica em Configurações do app &gt; Básico. Sem ele, nenhuma mensagem recebida
          é aceita aqui.
        </li>
        <li>
          Preencha os campos abaixo e clique em <strong>Salvar</strong>. Copie a URL de callback e o token de
          verificação (ele aparece uma única vez) para a configuração de Webhooks do {label} na Meta e assine o campo de
          mensagens.
        </li>
        <li>
          Clique em <strong>Testar conexão</strong>. Depois, mande uma mensagem para {messenger ? "a página" : "a conta"}
          : ela aparece em Conversas e o vendedor IA responde se estiver ligado.
        </li>
      </ol>
      <p className="muted">
        Aqui só é possível responder dentro de 24 h depois da última mensagem da pessoa, e quem escrever “SAIR” deixa de
        receber mensagens. A recuperação de vendas por template continua só no WhatsApp. Os nomes dos menus da Meta
        mudam com o tempo. Documentação oficial:{" "}
        <a href={DOCS[provider]} target="_blank" rel="noopener noreferrer">
          {label} — documentação da Meta
        </a>
        .
      </p>
    </details>
  );
}
