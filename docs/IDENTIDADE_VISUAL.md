# Identidade visual do AtendeVendeIA

Decidida em 07/10/2026 pelo Fábio (símbolo 3 da segunda rodada). Família de marcas: Iron Fit Core, by FM Tecnologia. As cores foram **medidas da
imagem da Iron Fit Core**; o símbolo é próprio (não copia o dela nem o do WhatsApp).

## Marca
- **Símbolo:** balão de conversa redondo, com anel duplo, as iniciais **AV** (A em prata, V em azul), três pontos de "digitando" e um selo de IA (faísca) no canto.
  Em tamanhos pequenos (até 36 px) usa a versão `compact` (sem anel fino e sem pontos), para continuar legível.
- **Nome:** AtendeVende em prata, **IA** em azul. É texto de verdade no painel (`apps/web/src/components/Brand.tsx`).
- **Slogan:** "Sua IA que atende, vende e recupera." (em maiúsculas com espaçamento nos lugares de destaque). Assinatura: "by FM Tecnologia".
- **Arquivos:** `apps/web/public/brand/logo-mark.svg` (símbolo), `apps/web/public/brand/logo-horizontal.svg` (símbolo, nome, slogan e assinatura) e o ícone
  da aba em `apps/web/src/app/icon.svg`. **Pendente:** o nome em `logo-horizontal.svg` ainda é texto (fonte do sistema); para material impresso ou site, converter as letras
  em curvas num programa de desenho, para ficar idêntico em qualquer lugar.

## Cores
| Papel | Cor | Uso |
|---|---|---|
| Fundo | `#000610` | fundo da página, texto sobre botão azul |
| Superfície | `#020E22` | cartões, menu lateral |
| Superfície 2 | `#07182F` | passar o mouse, áreas realçadas |
| Linha | `#1C3A60` | bordas de cartões |
| Linha de campo | `#5A7AA3` | bordas de campos e botões (contraste de componente) |
| Texto | `#E8ECF1` | texto principal |
| Texto suave | `#9FABBB` | explicações |
| Azul elétrico | `#2AA4F0` | destaque, links, botão (base) |
| Ciano | `#33D8FB` | brilho, item ativo do menu, botão (topo) |
| Prata | `#BABEC4` | marca, nome |
| Sucesso / Atenção / Erro | `#3ECF8E` / `#F0B429` / `#FF7B72` | estados |

**Contraste (medido):** todo texto passa de **7:1** sobre o fundo e sobre a superfície (nível AAA). Texto do botão azul é o fundo escuro (`#000610`, 7,4:1 a 12:1);
**não** usar texto branco sobre o azul (cerca de 2,7:1). Botões e campos têm borda mais clara (`#5A7AA3`) para serem vistos.

## Regras
- Tema **único escuro** (a marca é escura); o painel não segue mais o tema claro do sistema.
- Não prometer resultado absoluto em texto de marca ("nenhuma venda perdida"); a IA se apresenta como assistente virtual.
- Foco de teclado sempre visível (contorno ciano). Nada depende só de cor para ser entendido.
