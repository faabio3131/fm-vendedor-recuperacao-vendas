"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type KnowledgeItem } from "@/lib/api";

type Draft = { question: string; answer: string };
const EMPTY: Draft = { question: "", answer: "" };

export function KnowledgeCard({ canEdit }: { canEdit: boolean }) {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [note, setNote] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);

  const load = useCallback(() => {
    api<KnowledgeItem[]>("/v1/seller/knowledge").then(setItems).catch(() => undefined);
  }, []);
  useEffect(load, [load]);

  const run = async (action: () => Promise<string>) => {
    try {
      setNote({ kind: "ok", text: await action() });
      load();
    } catch (e) {
      setNote({ kind: "bad", text: e instanceof Error ? e.message : "Erro." });
    }
  };

  const add = () =>
    run(async () => {
      await api("/v1/seller/knowledge", { method: "POST", body: JSON.stringify(draft) });
      setDraft(EMPTY);
      return "Resposta cadastrada.";
    });

  const toggle = (k: KnowledgeItem) =>
    run(async () => {
      await api(`/v1/seller/knowledge/${k.id}`, { method: "PUT", body: JSON.stringify({ ...k, active: !k.active }) });
      return k.active ? "Resposta desativada." : "Resposta ativada.";
    });

  const remove = (k: KnowledgeItem) =>
    run(async () => {
      if (!window.confirm(`Remover a pergunta "${k.question}"?`)) return "Nada foi removido.";
      await api(`/v1/seller/knowledge/${k.id}`, { method: "DELETE" });
      return "Resposta removida.";
    });

  return (
    <>
      <h2>Conhecimento</h2>
      <p className="muted">
        Cadastre as dúvidas mais comuns dos seus clientes e a resposta certa. O vendedor responde por aqui, na hora, e
        só chama uma pessoa se o cliente pedir ou insistir no que não está cadastrado. Quanto mais completo, menos
        conversas chegam para a sua equipe.
      </p>
      {note && (
        <div className={`alert ${note.kind}`} role="status">
          {note.text}
        </div>
      )}
      {items.length === 0 && <p className="muted">Nenhuma resposta cadastrada ainda.</p>}
      {items.map((k) => (
        <div className="card" key={k.id} style={{ marginBottom: 12 }}>
          <div className="top" style={{ marginBottom: 0 }}>
            <strong>{k.question}</strong>
            <span className={`badge ${k.active ? "connected" : "locked"}`}>{k.active ? "Ativa" : "Inativa"}</span>
          </div>
          <div className="muted">{k.answer}</div>
          {canEdit && (
            <div className="row">
              <button onClick={() => void toggle(k)}>{k.active ? "Desativar" : "Ativar"}</button>
              <button className="danger" onClick={() => void remove(k)}>
                Remover
              </button>
            </div>
          )}
        </div>
      ))}
      {canEdit && (
        <div className="card">
          <strong>Nova resposta</strong>
          <label htmlFor="k-question">Pergunta do cliente</label>
          <input
            id="k-question"
            value={draft.question}
            maxLength={200}
            placeholder="Ex.: Como funciona a entrega?"
            onChange={(e) => setDraft({ ...draft, question: e.target.value })}
          />
          <label htmlFor="k-answer">Resposta</label>
          <textarea
            id="k-answer"
            value={draft.answer}
            maxLength={700}
            onChange={(e) => setDraft({ ...draft, answer: e.target.value })}
          />
          <div className="row">
            <button className="primary" onClick={() => void add()}>
              Cadastrar resposta
            </button>
          </div>
        </div>
      )}
    </>
  );
}
