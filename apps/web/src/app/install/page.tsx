"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";

type PromptoDeInstalacao = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: "accepted" | "dismissed" }> };

function Instalar() {
  const [prompto, setPrompto] = useState<PromptoDeInstalacao | null>(null);
  const [instalado, setInstalado] = useState(false);
  const [ios, setIos] = useState(false);

  useEffect(() => {
    setInstalado(window.matchMedia("(display-mode: standalone)").matches);
    setIos(/iphone|ipad|ipod/i.test(navigator.userAgent));
    const aoOferecer = (e: Event) => {
      e.preventDefault();
      setPrompto(e as PromptoDeInstalacao);
    };
    const aoInstalar = () => setInstalado(true);
    window.addEventListener("beforeinstallprompt", aoOferecer);
    window.addEventListener("appinstalled", aoInstalar);
    return () => {
      window.removeEventListener("beforeinstallprompt", aoOferecer);
      window.removeEventListener("appinstalled", aoInstalar);
    };
  }, []);

  async function instalar() {
    if (!prompto) return;
    await prompto.prompt();
    await prompto.userChoice;
    setPrompto(null);
  }

  return (
    <>
      <h1>App no celular</h1>
      <p className="muted">
        Instale o AtendeVendeIA na tela inicial do celular para abrir o painel em tela cheia, como um aplicativo, sem
        precisar de loja de aplicativos.
      </p>

      {instalado ? (
        <div className="alert" role="status">
          O app já está instalado neste aparelho.
        </div>
      ) : prompto ? (
        <div className="card">
          <p>Este aparelho pode instalar o app agora.</p>
          <button onClick={instalar}>Instalar o app</button>
        </div>
      ) : null}

      <div className="card">
        <strong>No Android (Chrome)</strong>
        <ol>
          <li>Abra o painel no Chrome.</li>
          <li>Toque nos três pontinhos, no canto de cima.</li>
          <li>Toque em <strong>Instalar app</strong> (ou <strong>Adicionar à tela inicial</strong>).</li>
        </ol>
      </div>

      <div className="card">
        <strong>No iPhone (Safari)</strong>
        {ios && <p className="muted">Você está num iPhone ou iPad: use o Safari para estes passos.</p>}
        <ol>
          <li>Abra o painel no Safari.</li>
          <li>Toque no botão de compartilhar (quadrado com uma seta para cima).</li>
          <li>Toque em <strong>Adicionar à Tela de Início</strong>.</li>
        </ol>
      </div>

      <p className="muted" style={{ marginTop: 16 }}>
        O app é o mesmo painel do navegador, com o mesmo login. Sem internet ele mostra um aviso e volta a funcionar
        quando a conexão voltar.
      </p>
    </>
  );
}

export default function Page() {
  return (
    <Shell>
      <Instalar />
    </Shell>
  );
}
