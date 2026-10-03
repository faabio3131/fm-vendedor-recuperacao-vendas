"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

const CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ?? "";
const DEV_LOGIN = process.env.NEXT_PUBLIC_DEV_LOGIN === "1";

type GoogleCredential = { credential: string };
type GoogleApi = {
  accounts: {
    id: {
      initialize: (cfg: { client_id: string; callback: (r: GoogleCredential) => void }) => void;
      renderButton: (el: HTMLElement, opts: Record<string, string | number>) => void;
    };
  };
};

export default function LoginPage() {
  const router = useRouter();
  const buttonRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(idToken: string) {
    setBusy(true);
    setError(null);
    try {
      await api("/v1/auth/google", { method: "POST", body: JSON.stringify({ id_token: idToken }) });
      router.replace("/");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Não foi possível entrar.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!CLIENT_ID) return;
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.onload = () => {
      const google = (window as unknown as { google?: GoogleApi }).google;
      if (!google || !buttonRef.current) return;
      google.accounts.id.initialize({ client_id: CLIENT_ID, callback: (r) => void send(r.credential) });
      google.accounts.id.renderButton(buttonRef.current, { theme: "outline", size: "large", width: 320 });
    };
    document.body.appendChild(script);
    return () => script.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="center">
      <div className="card login">
        <h1>Entrar</h1>
        <p className="muted">Use a conta Google do e-mail da sua compra.</p>
        {CLIENT_ID ? <div ref={buttonRef} /> : <div className="alert">Login com Google não configurado neste ambiente.</div>}
        {DEV_LOGIN && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void send(`sim|dev-${email}|${email}|Dev|1`);
            }}
          >
            <label htmlFor="dev-email">Login de desenvolvimento (e-mail)</label>
            <input id="dev-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            <div className="row">
              <button className="primary" disabled={busy}>
                Entrar (simulado)
              </button>
            </div>
          </form>
        )}
        {error && <div className="alert bad" role="alert">{error}</div>}
      </div>
    </div>
  );
}
