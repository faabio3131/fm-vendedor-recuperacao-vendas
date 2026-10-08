# App instalável (PWA)

O painel pode ser instalado no celular como aplicativo, **sem Play Store nem App Store** (decisão do Fábio, 08/10/2026).

## O que existe
- `apps/web/src/app/manifest.ts`: nome, cores da marca e ícones (`/manifest.webmanifest`).
- `apps/web/public/icons/`: ícones 192 e 512 px, 512 px "maskable" (Android) e `apple-touch-icon` (iPhone), gerados do símbolo AV.
- `apps/web/public/sw.js`: service worker mínimo. **Só** mostra `/offline.html` quando o aparelho está sem internet ao abrir uma página.
  **Não guarda** páginas, respostas da API nem conversas (painel autenticado, dados de clientes). Mudou o arquivo? Troque `VERSAO`.
- `apps/web/src/components/PwaRegister.tsx`: registra o service worker só no build de produção.
- Tela `/install` ("App no celular", no menu): botão de instalar quando o navegador oferece, e passos para Android (Chrome) e iPhone (Safari).
- `e2e/app-instalavel.spec.ts`: manifesto, ícones, cabeçalho do service worker, página offline e a tela de instalação.

## O que ainda NÃO existe
- **Notificações push** (aviso no celular quando a IA passa uma conversa para uma pessoa): precisam do domínio de produção em HTTPS, de
  chaves VAPID e de uma tela de permissão. Etapa seguinte, depois da produção.
- **Play Store:** se um dia for desejado, o mesmo PWA pode ser empacotado como TWA (conta de organização do Google Play, número D-U-N-S, e
  conferir a política de pagamento do Google, porque a assinatura é vendida fora da loja, na Cakto e na Hotmart).

## Como testar à mão
Abrir o painel no Chrome do Android (endereço em HTTPS, por exemplo o do staging): menu de três pontos, "Instalar app". No iPhone, Safari,
compartilhar, "Adicionar à Tela de Início". O service worker só é registrado em HTTPS (ou localhost) e no build de produção.
