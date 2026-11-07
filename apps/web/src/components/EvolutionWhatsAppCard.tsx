"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Provider } from "@/lib/api";

type PairingState = {
  status: "unavailable" | "not_configured" | "pending" | "connected" | "disconnected" | "needs_attention";
  connected: boolean;
  qr_code?: string;
  expires_in_seconds?: number;
};

type Props = { provider: Provider; canEdit: boolean };

const QR_POLL_MS = 10_000;
const PAIRING_WINDOW_MS = 5 * 60_000;

export function EvolutionWhatsAppCard({ provider, canEdit }: Props) {
  const [state, setState] = useState<PairingState | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<number | null>(null);
  const [left, setLeft] = useState(0);
  const [pairing, setPairing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastQrRef = useRef<string | null>(null);

  const clearQr = useCallback(() => {
    setQr(null);
    setExpiresAt(null);
  }, []);

  const check = useCallback(async () => {
    try {
      const status = await api<PairingState>("/v1/connections/evolution/status", { cache: "no-store" });
      setState(status);
      if (status.connected) {
        setPairing(false);
        lastQrRef.current = null;
        clearQr();
      }
      setError(null);
    } catch (e) {
      setState({ status: "needs_attention", connected: false });
      setError(e instanceof Error ? e.message : "Não foi possível conferir o WhatsApp.");
    }
  }, [clearQr]);

  useEffect(() => {
    if (!canEdit || !provider.enabled) return;
    void check();
  }, [canEdit, provider.enabled, check]);

  useEffect(() => {
    if (!pairing && state?.status !== "connected") return;
    const timer = window.setInterval(() => void check(), 5000);
    return () => window.clearInterval(timer);
  }, [pairing, state?.status, check]);

  useEffect(() => {
    if (!pairing) return;
    let cancelled = false;
    let inFlight = false;
    const refresh = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const data = await api<PairingState>("/v1/connections/evolution/qr", { cache: "no-store" });
        if (cancelled) return;
        setState(data);
        if (data.connected) {
          setPairing(false);
          lastQrRef.current = null;
          clearQr();
        } else if (data.qr_code?.startsWith("data:image/png;base64,") && data.expires_in_seconds) {
          // Não prolongar artificialmente a validade de um mesmo QR.
          if (data.qr_code !== lastQrRef.current) {
            lastQrRef.current = data.qr_code;
            setQr(data.qr_code);
            setExpiresAt(Date.now() + data.expires_in_seconds * 1000);
          }
          setError(null);
        } else {
          setError("Aguardando a Evolution disponibilizar um novo QR Code.");
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Falha ao renovar o QR Code.");
        }
      } finally {
        inFlight = false;
      }
    };

    const interval = window.setInterval(() => void refresh(), QR_POLL_MS);
    const deadline = window.setTimeout(() => {
      setPairing(false);
      clearQr();
      setError("Tempo de pareamento encerrado. Clique em Conectar WhatsApp para tentar novamente.");
    }, PAIRING_WINDOW_MS);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      window.clearTimeout(deadline);
    };
  }, [pairing, clearQr]);

  useEffect(() => {
    if (expiresAt === null) return;
    const tick = () => {
      const remaining = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
      setLeft(remaining);
      if (remaining === 0) clearQr();
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [expiresAt, clearQr]);

  async function connect() {
    if (!canEdit || busy || state?.status === "unavailable") return;
    setBusy(true);
    setPairing(false);
    setError(null);
    clearQr();
    lastQrRef.current = null;
    try {
      const data = await api<PairingState>("/v1/connections/evolution/connect", { method: "POST" });
      setState(data);
      if (data.connected) return;
      if (!data.qr_code?.startsWith("data:image/png;base64,") || !data.expires_in_seconds) {
        throw new Error("A Evolution ainda não disponibilizou um QR Code válido.");
      }
      lastQrRef.current = data.qr_code;
      setQr(data.qr_code);
      setLeft(data.expires_in_seconds);
      setExpiresAt(Date.now() + data.expires_in_seconds * 1000);
      setPairing(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao solicitar o QR Code.");
    } finally {
      setBusy(false);
    }
  }

  function stop() {
    setPairing(false);
    lastQrRef.current = null;
    clearQr();
    setError(null);
  }

  const label = state?.connected
    ? "WhatsApp conectado — sessão confirmada"
    : state?.status === "unavailable"
      ? "Integração ainda não liberada pela FM"
      : state?.status === "not_configured"
        ? "Nenhum WhatsApp conectado"
        : pairing
          ? "Aguardando pareamento no celular"
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
              {/* QR temporário da Evolution, nunca persistido nem exibido em logs. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={qr} alt="QR Code temporário para conectar WhatsApp" width={256} height={256}
                style={{ maxWidth: "100%", background: "#fff", padding: 12, borderRadius: 12 }} />
              <p>Exibição por até {left}s. O QR é renovado automaticamente e pode expirar antes.</p>
            </div>
          )}
          {pairing && !qr && (
            <p className="muted" role="status">Aguardando o próximo QR Code válido da Evolution…</p>
          )}
          <div className="row">
            <button className="primary" type="button" onClick={() => void connect()}
              disabled={busy || state?.status === "unavailable"}>
              {busy ? "Solicitando…" : pairing ? "Reiniciar pareamento" : "Conectar WhatsApp"}
            </button>
            {pairing && <button type="button" onClick={stop}>Parar tentativa</button>}
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
