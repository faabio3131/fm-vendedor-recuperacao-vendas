"use client";

import { useState } from "react";
import { api, ApiError, type Connection, type Provider } from "@/lib/api";
import { MetaChannelGuide } from "@/components/MetaChannelGuide";
import { WhatsAppGuide } from "@/components/WhatsAppGuide";

const STATUS: Record<Connection["status"], string> = {
  pending: "Aguardando teste",
  connected: "Conectada",
  needs_attention: "Precisa de atenção",
  disconnected: "Desconectada",
};

type Props = {
  provider: Provider;
  connection: Connection | undefined;
  canEdit: boolean;
  onChange: () => void;
};

export function ProviderCard({ provider, connection, canEdit, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const [secretOnce, setSecretOnce] = useState<string | null>(null);

  const locked = !provider.enabled;

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setNote(null);
    try {
      await action();
    } catch (e) {
      setNote({ kind: "bad", text: e instanceof ApiError || e instanceof Error ? e.message : "Erro." });
    } finally {
      setBusy(false);
    }
  }

  const save = () =>
    run(async () => {
      const res = await api<Connection>(`/v1/connections/${provider.key}`, {
        method: "PUT",
        body: JSON.stringify({ values }),
      });
      setValues({});
      setSecretOnce(res.webhook_secret_once ?? null);
      setNote({ kind: "ok", text: "Salvo. Faça o teste para confirmar a conexão." });
      onChange();
    });

  const test = () =>
    run(async () => {
      const res = await api<Connection>(`/v1/connections/${provider.key}/test`, { method: "POST" });
      setNote({ kind: res.status === "connected" ? "ok" : "bad", text: res.message ?? STATUS[res.status] });
      onChange();
    });

  const remove = () =>
    run(async () => {
      if (!window.confirm(`Remover a conexão com ${provider.name}?`)) return;
      await api(`/v1/connections/${provider.key}`, { method: "DELETE" });
      setSecretOnce(null);
      setOpen(false);
      onChange();
    });

  return (
    <div className="card">
      <div className="top" style={{ marginBottom: 6 }}>
        <strong>{provider.name}</strong>
        {locked ? (
          <span className="badge locked">Plano {provider.phase}</span>
        ) : (
          <span className={`badge ${connection?.status ?? "pending"}`}>
            {connection ? STATUS[connection.status] : "Não configurada"}
          </span>
        )}
      </div>
      <p className="muted">{provider.description}</p>
      {connection?.last_error && <p className="muted">{connection.last_error}</p>}

      {locked ? (
        <p className="muted">Disponível a partir do plano {provider.phase}.</p>
      ) : (
        <button onClick={() => setOpen((v) => !v)} disabled={!canEdit && !connection} aria-expanded={open}>
          {open ? "Fechar" : connection ? "Gerenciar" : "Configurar"}
        </button>
      )}

      {open && !locked && (
        <div>
          {provider.key === "whatsapp_cloud" && <WhatsAppGuide />}
          {(provider.key === "messenger" || provider.key === "instagram_dm") && (
            <MetaChannelGuide provider={provider.key} />
          )}
          {provider.fields.map((f) => (
            <div key={f.key}>
              <label htmlFor={`${provider.key}-${f.key}`}>
                {f.label}
                {f.required ? "" : " (opcional)"}
              </label>
              <input
                id={`${provider.key}-${f.key}`}
                type={f.secret ? "password" : "text"}
                autoComplete="off"
                disabled={!canEdit}
                value={values[f.key] ?? ""}
                placeholder={connection?.values[f.key] || ""}
                onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
              />
              {f.secret && connection && (
                <div className="muted">Deixe em branco para manter o valor salvo.</div>
              )}
            </div>
          ))}

          {provider.webhook && connection?.webhook_url && (
            <div className="alert">
              Cole esta URL no painel de {provider.name}:
              <div>
                <code>{connection.webhook_url}</code>
              </div>
              {!provider.generates_secret ? (
                <div className="muted">
                  Use na plataforma o mesmo segredo que você informou acima. Ele só é conferido aqui, nunca exibido.
                </div>
              ) : secretOnce ? (
                <div>
                  Segredo do webhook, também usado como token de verificação (aparece só agora, guarde): <code>{secretOnce}</code>
                </div>
              ) : (
                <div className="muted">O segredo do webhook já foi gerado e não é exibido de novo.</div>
              )}
            </div>
          )}

          {canEdit ? (
            <div className="row">
              <button className="primary" onClick={save} disabled={busy}>
                Salvar
              </button>
              {connection && (
                <button onClick={test} disabled={busy}>
                  Testar conexão
                </button>
              )}
              {connection && (
                <button className="danger" onClick={remove} disabled={busy}>
                  Remover
                </button>
              )}
            </div>
          ) : (
            <p className="muted">Só dono ou administrador altera conexões.</p>
          )}
        </div>
      )}
      {note && (
        <div className={`alert ${note.kind}`} role="status">
          {note.text}
        </div>
      )}
    </div>
  );
}
