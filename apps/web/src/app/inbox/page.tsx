"use client";

import { useCallback, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { api, type Channel, type ConversationDetail, type ConversationRow } from "@/lib/api";

const CHANNEL: Record<Channel, string> = { whatsapp: "WhatsApp", messenger: "Messenger", instagram: "Instagram" };

const STATUS: Record<ConversationRow["status"], string> = {
  bot: "Vendedor IA",
  human: "Aguardando pessoa",
  closed: "Encerrada",
};
const REASON: Record<string, string> = {
  ia_desligada: "Vendedor IA desligado",
  ia_indisponivel: "Modelo de IA indisponível",
  pediu_atendente: "Cliente pediu uma pessoa",
  sem_ofertas: "Sem oferta ativa",
  resposta_invalida: "Resposta da IA recusada pelas regras",
  limite_de_respostas: "Limite de respostas por hora",
  assinatura_inativa: "Assinatura inativa: vendedor IA pausado",
};
const AUTHOR: Record<string, string> = {
  customer: "Cliente",
  bot: "Vendedor IA",
  human: "Equipe",
  recovery: "Recuperação",
  system: "Sistema",
};
const DELIVERY: Record<string, string> = {
  queued: "na fila",
  sending: "enviando",
  sent: "enviada",
  delivered: "entregue",
  read: "lida",
  failed: "não enviada",
};
const FAILURE: Record<string, string> = {
  janela_24h_fechada: "Passou de 24 h desde a última mensagem do cliente",
  assinatura_inativa: "Assinatura inativa: vendedor IA pausado",
  sem_id: "Contato sem identificação no canal",
  canal_sem_adaptador: "Canal sem envio habilitado",
  nao_contatar: "Contato pediu para não receber mensagens",
  canal_nao_conectado: "WhatsApp não conectado",
};

function Detail({ id, onChange }: { id: string; onChange: () => void }) {
  const [d, setD] = useState<ConversationDetail | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<ConversationDetail>(`/v1/inbox/${id}`).then(setD).catch((e: unknown) => setError(e instanceof Error ? e.message : "Erro."));
  }, [id]);
  useEffect(load, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      setText("");
      load();
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erro.");
    }
  };
  if (!d) return <div className="card muted">{error ?? "Carregando…"}</div>;
  return (
    <div className="card">
      <div className="top" style={{ marginBottom: 8 }}>
        <div>
          <strong>{d.name || "Sem nome"}</strong> <span className="muted">{d.phone}</span>{" "}
          <span className="badge">{CHANNEL[d.channel]}</span>
        </div>
        <span className={`badge ${d.status === "human" ? "pending" : ""}`}>{STATUS[d.status]}</span>
      </div>
      <div aria-label="Mensagens" style={{ display: "grid", gap: 8, maxHeight: 360, overflowY: "auto" }}>
        {d.messages.map((m) => (
          <div key={m.id} style={{ justifySelf: m.direction === "in" ? "start" : "end", maxWidth: "85%" }}>
            <div className="muted" style={{ fontSize: 12 }}>
              {AUTHOR[m.author]}
              {m.direction === "out" && ` · ${DELIVERY[m.status] ?? m.status}`}
            </div>
            <div className="card" style={{ padding: "8px 10px" }}>
              {m.body}
            </div>
            {m.error && <div className="muted">{FAILURE[m.error] ?? m.error}</div>}
          </div>
        ))}
      </div>
      {d.status !== "closed" && (
        <>
          <label htmlFor="reply">Responder como equipe</label>
          <textarea id="reply" value={text} maxLength={1000} onChange={(e) => setText(e.target.value)} disabled={!d.window_open} />
          {!d.window_open && (
            <div className="muted">
              Passou de 24 h desde a última mensagem do cliente:{" "}
              {d.channel === "whatsapp" ? "só é possível enviar template aprovado." : "a Meta não permite resposta livre."}
            </div>
          )}
        </>
      )}
      <div className="row">
        {d.status !== "closed" && (
          <button
            className="primary"
            disabled={!d.window_open || text.trim() === ""}
            onClick={() => void act(() => api(`/v1/inbox/${id}/reply`, { method: "POST", body: JSON.stringify({ body: text }) }))}
          >
            Enviar
          </button>
        )}
        {d.status !== "bot" && (
          <button onClick={() => void act(() => api(`/v1/inbox/${id}/status`, { method: "POST", body: JSON.stringify({ status: "bot" }) }))}>
            Devolver ao vendedor IA
          </button>
        )}
        {d.status !== "closed" && (
          <button onClick={() => void act(() => api(`/v1/inbox/${id}/status`, { method: "POST", body: JSON.stringify({ status: "closed" }) }))}>
            Encerrar
          </button>
        )}
      </div>
      {error && (
        <div className="alert bad" role="status">
          {error}
        </div>
      )}
    </div>
  );
}

function Inbox() {
  const [rows, setRows] = useState<ConversationRow[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const load = useCallback(() => {
    api<ConversationRow[]>("/v1/inbox").then(setRows).catch(() => setRows([]));
  }, []);
  useEffect(load, [load]);
  return (
    <>
      <h1>Conversas</h1>
      <p className="muted">Quem escreveu no WhatsApp. As que precisam de uma pessoa aparecem primeiro.</p>
      {rows && rows.length === 0 && <div className="alert">Nenhuma conversa ainda. Elas aparecem quando um cliente escreve para o seu número.</div>}
      <div style={{ display: "grid", gap: 8 }}>
        {(rows ?? []).map((r) => (
          <div key={r.id}>
            <button
              style={{ width: "100%", textAlign: "left" }}
              aria-expanded={open === r.id}
              onClick={() => setOpen(open === r.id ? null : r.id)}
            >
              <strong>{r.name || r.phone}</strong> <span className="badge">{CHANNEL[r.channel]}</span>{" "}
              <span className={`badge ${r.status === "human" ? "pending" : ""}`}>{STATUS[r.status]}</span>
              <div className="muted">
                {r.handoff_reason && r.status === "human" ? `${REASON[r.handoff_reason] ?? r.handoff_reason} · ` : ""}
                {r.preview}
              </div>
            </button>
            {open === r.id && <Detail id={r.id} onChange={load} />}
          </div>
        ))}
      </div>
    </>
  );
}

export default function Page() {
  return (
    <Shell>
      <Inbox />
    </Shell>
  );
}
