"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Shell, useMe } from "@/components/Shell";
import { api, brl, type Connection, type Onboarding, type RecoverySummary } from "@/lib/api";

const STATUS: Record<Connection["status"], string> = {
  pending: "Aguardando teste",
  connected: "Conectada",
  needs_attention: "Precisa de atenção",
  disconnected: "Desconectada",
};

function Overview() {
  const me = useMe();
  const [connections, setConnections] = useState<Connection[] | null>(null);
  const [recovery, setRecovery] = useState<RecoverySummary | null>(null);
  const [onboarding, setOnboarding] = useState<Onboarding | null>(null);

  useEffect(() => {
    api<RecoverySummary>("/v1/recovery/summary").then(setRecovery).catch(() => setRecovery(null));
    api<Onboarding>("/v1/onboarding").then(setOnboarding).catch(() => setOnboarding(null));
    api<Connection[]>("/v1/connections").then(setConnections).catch(() => setConnections([]));
  }, []);

  const planName = me.plan.key ? me.plan.key.replace("fase-", "Plano ") : "Sem plano";

  return (
    <>
      <h1>Visão geral</h1>
      <p className="muted">Seu plano e o estado das conexões.</p>

      {onboarding && onboarding.done < onboarding.total && (
        <div className="card" aria-label="Progresso dos primeiros passos">
          <strong>
            Primeiros passos: {onboarding.done} de {onboarding.total}
          </strong>
          <div
            className="progress"
            role="progressbar"
            aria-label="Progresso geral"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={onboarding.percent}
          >
            <span style={{ width: `${onboarding.percent}%` }} />
          </div>
          <div className="muted">
            Próximo: {onboarding.steps.find((s) => !s.done)?.title}.{" "}
            <Link href="/onboarding">Ver o que falta</Link>
          </div>
        </div>
      )}

      <div className="grid">
        <div className="card">
          <div className="muted">Plano</div>
          <strong>{planName}</strong>
          <div className="muted">{me.plan.status === "active" ? "Ativo" : me.plan.status}</div>
        </div>
        {recovery && (
          <div className="card">
            <div className="muted">Recuperado (30 dias)</div>
            <strong>{brl(recovery.recovered_cents)}</strong>
            <div className="muted">
              <Link href="/recovery">{recovery.cases_by_status.recovered} vendas recuperadas</Link>
            </div>
          </div>
        )}
        <div className="card">
          <div className="muted">Conexões</div>
          <strong>{connections === null ? "…" : connections.length}</strong>
          <div className="muted">configuradas</div>
        </div>
      </div>

      <h2>Conexões</h2>
      {connections && connections.length === 0 && (
        <div className="alert">
          Nenhuma conexão ainda. Comece conectando o WhatsApp em <Link href="/connections">Conexões</Link>.
        </div>
      )}
      <div className="grid">
        {(connections ?? []).map((c) => (
          <div className="card" key={c.provider}>
            <strong>{c.provider}</strong>
            <div>
              <span className={`badge ${c.status}`}>{STATUS[c.status]}</span>
            </div>
            {c.last_error && <p className="muted">{c.last_error}</p>}
          </div>
        ))}
      </div>
    </>
  );
}

export default function Page() {
  return (
    <Shell>
      <Overview />
    </Shell>
  );
}
