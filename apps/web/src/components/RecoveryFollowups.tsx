"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, brl, type RecoveryFollowup } from "@/lib/api";

const REASONS: Record<RecoveryFollowup["reason"], string> = {
  evolution_outside_window:
    "Fora da janela de contato automático pela Evolution. Encaminhe para análise humana.",
  cloud_template_unavailable:
    "O template da Meta ainda não está aprovado ou cadastrado. Confira a área Templates.",
};

export function RecoveryFollowups({
  version,
  onChange,
  canEdit,
}: {
  version: number;
  onChange: () => void;
  canEdit: boolean;
}) {
  const [tasks, setTasks] = useState<RecoveryFollowup[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api<RecoveryFollowup[]>("/v1/recovery/followups?status=pending&limit=100")
      .then((data) => { if (active) setTasks(data); })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : "Não foi possível carregar.");
      });
    return () => { active = false; };
  }, [version]);

  async function close(id: string, action: "reviewed" | "dismissed") {
    setError(null);
    setBusy(id);
    try {
      await api(`/v1/recovery/followups/${id}/resolve`, {
        method: "POST", body: JSON.stringify({ action }),
      });
      setTasks((current) => current.filter((t) => t.id !== id));
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Não foi possível atualizar a tarefa.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-label="Acompanhamentos de longo prazo" id="acompanhamentos">
      <h2>Acompanhamentos de longo prazo</h2>
      <p className="muted">
        Quando um envio automático não é permitido, o sistema preserva a oportunidade
        para revisão pela equipe. Nenhuma tarefa desta lista envia WhatsApp automaticamente.
        Verifique autorização para contato, preferências do cliente e o canal apropriado.
      </p>
      {error && <div role="alert" className="alert bad">{error}</div>}
      {tasks.length === 0 && !error && (
        <p className="muted">Nenhum acompanhamento manual pendente.</p>
      )}
      {tasks.length > 0 && (
        <div className="card tablewrap">
          <table>
            <thead>
              <tr><th>Contato</th><th>Produto</th><th>Motivo</th><th>Agendado</th><th>Ação</th></tr>
            </thead>
            <tbody>
              {tasks.map((task) => (
                <tr key={task.id}>
                  <td>
                    <strong>{task.contact.name || "Cliente"}</strong>
                    <div className="muted">{task.contact.phone}</div>
                    {task.contact.email && <div className="muted">{task.contact.email}</div>}
                  </td>
                  <td>
                    {task.product || "Negociação"}
                    <div className="muted">{brl(task.amount_cents)}</div>
                  </td>
                  <td>
                    {REASONS[task.reason]}
                    <div className="muted">Modelo previsto: {task.template_key}</div>
                    {task.reason === "cloud_template_unavailable" && (
                      <div><a href="#templates">Revisar templates</a></div>
                    )}
                  </td>
                  <td>{new Date(task.created_at).toLocaleString("pt-BR")}</td>
                  <td>
                    <div className="row" style={{ flexWrap: "wrap" }}>
                      <Link href="/inbox">Ver conversas</Link>
                      {canEdit && (
                        <>
                          <button disabled={busy !== null}
                            onClick={() => void close(task.id, "reviewed")}>
                            Marcar revisado
                          </button>
                          <button disabled={busy !== null}
                            onClick={() => void close(task.id, "dismissed")}>
                            Dispensar
                          </button>
                        </>
                      )}
                    </div>
                    <small className="muted">Marcar revisado não significa mensagem enviada.</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
