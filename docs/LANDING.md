# Landing page do AtendeVendeIA

Página de apresentação **estática** (HTML, CSS e um pouco de JavaScript, sem dependências e sem coleta de dados), em `apps/landing/`. Criada em 08/10/2026,
com o acesso **bloqueado** enquanto a produção paga não estiver no ar.

## Arquivos
- `index.html`: a página (símbolo, herói com conversa de exemplo, como funciona, controle, para quem, segurança, perguntas, chamada final, rodapé).
- `styles.css`: cores e estilos da marca (`docs/IDENTIDADE_VISUAL.md`).
- `config.js`: **o único arquivo a editar para liberar** e para preencher CNPJ, sede, contato e links dos termos e da privacidade.
- `landing.js`: aplica o `config.js`. Sem ele (ou se o `config.js` falhar), a página fica **bloqueada**.
- `assets/`: ícone da aba e símbolo.

## Estado atual (bloqueado)
Todos os botões de entrada mostram "Acesso em breve" e não têm link. O rodapé mostra só "FM Tecnologia LTDA"; CNPJ, sede, contato, Termos e Privacidade
**ficam escondidos** até serem preenchidos no `config.js` (nada de "preencher depois" aparece para o visitante). O `index.html` tem `noindex` para não
aparecer em buscadores enquanto estiver bloqueada.

## Como publicar (escolha uma)
1. **Dentro do site da FM Tecnologia:** copiar a pasta `apps/landing/` inteira para uma pasta do site (por exemplo `/atendevendeia/`). Funciona em qualquer hospedagem de
   arquivos. Se o site tiver política de segurança de conteúdo (CSP), ela precisa permitir script e estilo do próprio endereço (os arquivos são separados, sem script embutido).
2. **Subdomínio** (por exemplo `atendevendeia.fmtecnologia.com.br`): publicar `apps/landing/` como site estático (no Render, "Static Site", diretório `apps/landing`) e criar o
   apontamento (CNAME) no provedor do domínio. Precisa de acesso ao DNS.
3. **Incorporar numa página do site** (WordPress ou similar): usar o conteúdo do `index.html` e subir `styles.css`, `config.js`, `landing.js` e `assets/` na biblioteca de arquivos.

## Como liberar (quando a produção paga estiver no ar)
1. No `config.js`: `liberado: true` e `painel: "https://<painel de produção>/login"` (tem de começar com `https://`).
2. Preencher `cnpj`, `sede`, `contato`, `linkTermos` e `linkPrivacidade` (textos aprovados).
3. No `index.html`, **remover** a linha `<meta name="robots" content="noindex, nofollow">`.
4. Ajustar os textos "Quanto custa?" e "Quando abre?" (perguntas) e o selo "Em breve" do herói, que hoje dizem que os planos e a abertura estão em definição.
5. Conferir no celular e no computador; testar o botão de entrada.

## Regras de conteúdo
- Só prometer o que existe e foi provado; sem número de resultado nem promessa absoluta ("nenhuma venda perdida"). A IA se apresenta como assistente virtual.
- Hoje a página fala só de **WhatsApp**: Messenger, Instagram e Hotmart não foram testados com contas reais e não aparecem.
- Sem preço até a decisão dos planos. Sem formulário: não coleta dado pessoal (se um dia coletar, entra aviso de privacidade e a base legal).
- WhatsApp é marca da Meta; o rodapé diz que o produto não é afiliado nem endossado por ela. Não usar o logotipo do WhatsApp.
