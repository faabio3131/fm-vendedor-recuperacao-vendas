"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type HandoffReport, type SandboxResult, type SandboxTurn } from "@/lib/api";

const MAX_TURNS = 12;

/** Conversa de teste: mostra o que o vendedor faria, sem enviar nada nem gastar o limite. */
export function SandboxCard() {
  const [turns, setTurns] = useState<SandboxTurn[]>([]);
  const [text, setText] = useState("");
  const [result, setResult] = useState<SandboxResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const full = turns.length >= MAX_TURNS - 1;

  async function send() {
    const message = text.trim();
    if (!message || busy) return;
    const next: SandboxTurn[] = [...turns, { role: "customer", text: message }];
    setBusy(true);
    setError(null);
    try {
      const res = await api<SandboxResult>("/v1/seller/sandbox", {
        method: "POST",
        body: JSON.stringify({ messages: next }),
      });
      setResult(res);
      setTurns(res.outcome === "reply" ? [...next, { role: "assistant", text: res.text }] : next);
      setText("");
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : "Não foi possível testar.");
    } finally {
      setBusy(false);
    }
  }

  function clear() {
    setTurns([]);
    setResult(null);
    setError(null);
    setText("");
  }

  return (
    <section className="card" aria-labelledby="sandbox-title" style={{ marginTop: 16 }}>
      <h2 id="sandbox-title" style={{ marginTop: 0 }}>
        Testar conversa
      </h2>
      <p className="muted">
        Escreva como se fosse um cliente e veja o que o vendedor faria. É uma simulação: nada é enviado a
        ninguém, nada é gravado e não conta no limite do seu plano. A IA de verdade pode responder com
        outras palavras, mas preço e link sempre vêm das suas ofertas.
      </p>
      {turns.length > 0 && (
        <ol className="steps" aria-label="Conversa de teste">
          {turns.map((t, i) => (
            <li key={i}>
              <span className={`mark ${t.role === "assistant" ? "done" : ""}`} aria-hidden="true">
                {t.role === "assistant" ? "IA" : "C"}
              </span>
              <div className="body">
                <strong>{t.role === "assistant" ? "Vendedor" : "Cliente (teste)"}</strong>
                <div>{t.text}</div>
              </div>
            </li>
          ))}
        </ol>
      )}
      <label htmlFor="sandbox-msg">Mensagem do cliente (teste)</label>
      <textarea
        id="sandbox-msg"
        maxLength={500}
        value={text}
        disabled={full}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="row">
        <button className="primary" disabled={busy || full || !text.trim()} onClick={() => void send()}>
          Testar
        </button>
        <button onClick={clear}>Limpar conversa</button>
      </div>
      {full && <p className="muted">A conversa de teste chegou ao limite. Limpe para recomeçar.</p>}
      {error && (
        <div className="alert bad" role="status">
          {error}
        </div>
      )}
      {result && !error && (
        <div className={`alert ${result.outcome === "reply" ? "ok" : ""}`} role="status">
          {result.outcome === "reply" && (
            <>
              O vendedor responderia{result.offer ? ` (oferta: ${result.offer})` : ""}: {result.text}
            </>
          )}
          {result.outcome === "handoff" && <>Passaria para uma pessoa: {result.reason_label}.</>}
          {result.outcome === "silence" && <>Não responderia. {result.reason_label}</>}
          {!result.ai_enabled && (
            <div className="muted">O vendedor IA está desligado em Ajustes: na prática, a conversa iria para uma pessoa.</div>
          )}
        </div>
      )}
    </section>
  );
}

/** Por que as conversas foram para uma pessoa, para o cliente melhorar as ofertas. */
export function HandoffReportCard() {
  const [report, setReport] = useState<HandoffReport | null>(null);

  useEffect(() => {
    api<HandoffReport>("/v1/seller/handoffs?days=30")
      .then(setReport)
      .catch(() => setReport(null));
  }, []);

  return (
    <section className="card" aria-labelledby="handoff-title" style={{ marginTop: 16 }}>
      <h2 id="handoff-title" style={{ marginTop: 0 }}>
        Por que passou para uma pessoa
      </h2>
      <p className="muted">Conversas dos últimos 30 dias que o vendedor entregou para a equipe.</p>
      {report && report.items.length === 0 && <p>Nenhuma transferência nos últimos 30 dias.</p>}
      {report && report.items.length > 0 && (
        <ul className="steps" aria-label="Motivos de transferência">
          {report.items.map((i) => (
            <li key={i.reason}>
              <span className="mark" aria-hidden="true">
                {i.count}
              </span>
              <div className="body">
                <strong>{i.label}</strong>
                {i.tip && <div className="muted">{i.tip}</div>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
