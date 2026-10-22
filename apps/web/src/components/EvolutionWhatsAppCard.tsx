"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Provider } from "@/lib/api";

type PairingState = {
  status: "unavailable" | "not_configured" | "pending" | "connected" | "disconnected" | "needs_attention";
  connected: boolean;
  qr_code?: string;
  expires_in_seconds?: number;
};

type Props = { provider: Provider; canEdit: boolean };

export function EvolutionWhatsAppCard({ provider, canEdit }: Props) {
  const [state, setState] = useState<PairingState | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<number | null>(null);
  const [left, setLeft] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(async () => {
    try {
      const status = await api<PairingState>("/v1/connections/evolution/status");
      setState(status);
      if (status.connected) {
        setQr(null);
        setExpiresAt(null);
      }
      setError(null);
    } catch (e) {
      setState({ status: "needs_attention", connected: false });
      setError(e instanceof Error ? e.message : "Não foi possível conferir o WhatsApp.");
    }
  }, []);

  useEffect(() => {
    if (!canEdit || !provider.enabled) return;
    void check();
  }, [canEdit, provider.enabled, check]);

  useEffect(() => {
    if (!qr && state?.status !== "connected") return;
    const timer = window.setInterval(() => void check(), 5000);
    return () => window.clearInterval(timer);
  }, [qr, state?.status, check]);

  useEffect(() => {
    if (expiresAt === null) return;
    const tick = () => {
      const remaining = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
      setLeft(remaining);
      if (remaining === 0) {
        setQr(null);
        setExpiresAt(null);
      }
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [expiresAt]);

  async function connect() {
    if (!canEdit || busy || state?.status === "unavailable") return;
    setBusy(true);
    setError(null);
    setQr(null);
    setExpiresAt(null);
    try {
      const data = await api<PairingState>("/v1/connections/evolution/connect", { method: "POST" });
      setState(data);
      if (data.connected) return;
      if (!data.qr_code?.startsWith("data:image/png;base64,") || !data.expires_in_seconds) {
        throw new Error("A Evolution ainda não disponibilizou um QR Code válido.");
      }
      setQr(data.qr_code);
      setLeft(data.expires_in_seconds);
      setExpiresAt(Date.now() + data.expires_in_seconds * 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao solicitar o QR Code.");
    } finally {
      setBusy(false);
    }
  }

  const label = state?.connected
    ? "WhatsApp conectado — sessão confirmada"
    : state?.status === "unavailable"
      ? "Integração ainda não liberada pela FM"
      : state?.status === "not_configured"
        ? "Nenhum WhatsApp conectado"
        : state?.status === "pending"
          ? "Aguardando leitura do QR Code"
          : state?.status === "needs_attention"
            ? "Conexão não verificável"
            : "WhatsApp desconectado";

  return (
    <div className="card">
      <div className="top" style={{ marginBottom: 6 }}>
        <strong>{provider.name}</strong>
        <span className={`badge ${state?.connected ? "connected" : "pending"}`}>
          {state?.connected ? "Conectada" : "Não confirmada"}
        </span>
      </div>
      <p className="muted">{provider.description}</p>
      <p role="status">{label}</p>
      {!provider.enabled ? (
        <p className="muted">Disponível a partir do plano {provider.phase}.</p>
      ) : !canEdit ? (
        <p className="muted">Somente proprietário ou administrador pode parear um número.</p>
      ) : (
        <>
          {qr && left > 0 && (
            <div className="alert">
              <p>Abra WhatsApp no celular → Aparelhos conectados → Conectar aparelho.</p>
              {/* A imagem temporária vem da Evolution, via backend autenticado. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={qr} alt="QR Code temporário para conectar WhatsApp" width={256} height={256}
                style={{ maxWidth: "100%", background: "#fff", padding: 12, borderRadius: 12 }} />
              <p>QR temporário — atualização em {left}s. Não compartilhe este código.</p>
            </div>
          )}
          <div className="row">
            <button className="primary" type="button" onClick={() => void connect()}
              disabled={busy || state?.status === "unavailable"}>
              {busy ? "Solicitando…" : qr ? "Gerar novo QR Code" : "Conectar WhatsApp"}
            </button>
            <button type="button" onClick={() => void check()} disabled={busy}>
              Verificar conexão
            </button>
          </div>
          {error && <div className="alert bad" role="alert">{error}</div>}
          <p className="muted">
            O pareamento depende da Evolution API na Contabo. Envio real continua bloqueado
            até a certificação da integração e das políticas de contato.
          </p>
        </>
      )}
    </div>
  );
}
