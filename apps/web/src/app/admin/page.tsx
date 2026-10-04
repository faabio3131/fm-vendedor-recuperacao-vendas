"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  type AdminClients,
  type AdminHealth,
  type AdminMetrics,
} from "@/lib/api";

const PLAN_STATUS: Record<string, string> = {
  active: "Ativa",
  past_due: "Em atraso",
  suspended: "Suspensa",
  canceled: "Cancelada",
  refunded: "Reembolsada",
};

function message(e: unknown): string {
  return e instanceof ApiError || e instanceof Error ? e.message : "Algo deu errado.";
}

function age(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 90) return `${seconds} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  return `${Math.round(seconds / 3600)} h`;
}

function Admin() {
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);
  const [clients, setClients] = useState<AdminClients | null>(null);
  const [metrics, setMetrics] = useState<AdminMetrics | null>(null);
  const [health, setHealth] = useState<AdminHealth | null>(null);
  const [note, setNote] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const [plans, setPlans] = useState<Record<string, string>>({});

  const load = useCallback(() => {
    api<AdminClients>("/v1/admin/clients?limit=100").then(setClients).catch(() => undefined);
    api<AdminMetrics>("/v1/admin/metrics").then(setMetrics).catch(() => undefined);
    api<AdminHealth>("/v1/admin/health").then(setHealth).catch(() => undefined);
  }, []);

  useEffect(() => {
    api<{ email: string }>("/v1/admin/me")
      .then((me) => {
        setEmail(me.email);
        load();
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 401) router.replace("/login");
        else setDenied(true);
      });
  }, [router, load]);

  async function act(path: string, init: RequestInit, done: string) {
    setNote(null);
    try {
      await api(path, init);
      setNote({ kind: "ok", text: done });
      load();
    } catch (e) {
      setNote({ kind: "bad", text: message(e) });
    }
  }

  async function logout() {
    await api("/v1/auth/logout", { method: "POST" }).catch(() => undefined);
    router.replace("/login");
  }

  if (denied) {
    return (
      <div className="center">
        <div className="card login">
          <h1>Sem acesso</h1>
          <p className="muted">Esta área é só para a equipe da plataforma.</p>
          <Link href="/">Voltar ao painel</Link>
        </div>
      </div>
    );
  }
  if (!email) return <div className="center muted">Carregando…</div>;

  return (
    <main className="main" style={{ margin: "0 auto" }}>
      <div className="top">
        <div>
          <strong>Administração da plataforma</strong>
          <div className="muted">{email}</div>
        </div>
        <div className="row" style={{ marginTop: 0 }}>
          <Link className="btn" href="/">
            Painel do cliente
          </Link>
          <button onClick={() => void logout()}>Sair</button>
        </div>
      </div>
      <h1>Operação</h1>
      <p className="muted">
        Visão de todos os clientes. Aqui não aparece conversa, contato nem credencial de ninguém; cada ação fica na
        auditoria do cliente com o seu nome.
      </p>
      {note && (
        <div className={`alert ${note.kind}`} role="status">
          {note.text}
        </div>
      )}

      <section className="card" aria-labelledby="health-title">
        <h2 id="health-title" style={{ marginTop: 0 }}>
          Saúde
        </h2>
        {!health && <p className="muted">Carregando…</p>}
        {health && health.ok && <p>Tudo em ordem: nada parado nem falhando agora.</p>}
        {health &&
          health.findings.map((f) => (
            <div key={f.code} className={`alert ${f.level === "critical" ? "bad" : ""}`}>
              <strong>{f.level === "critical" ? "Crítico" : "Aviso"}:</strong> {f.message}
            </div>
          ))}
      </section>

      {metrics && (
        <section aria-label="Números do sistema" style={{ marginTop: 16 }}>
          <div className="grid">
            <div className="card">
              <div className="muted">Worker</div>
              <strong>{age(metrics.worker.last_cycle_age_s)}</strong>
              <div className="muted">desde o último ciclo · {metrics.worker.cycles} ciclos</div>
            </div>
            <div className="card">
              <div className="muted">Fila de saída</div>
              <strong>{metrics.outbox.queued}</strong>
              <div className="muted">
                na fila (mais antiga: {age(metrics.outbox.oldest_queued_age_s)}) · {metrics.outbox.sent_24h} enviadas e{" "}
                {metrics.outbox.failed_24h} falhas em 24 h
              </div>
            </div>
            <div className="card">
              <div className="muted">Recuperação</div>
              <strong>{metrics.recovery.steps_overdue}</strong>
              <div className="muted">
                passos atrasados · {metrics.recovery.steps_sent_24h} enviados e {metrics.recovery.steps_failed_24h} falhas
                em 24 h
              </div>
            </div>
            <div className="card">
              <div className="muted">Eventos recebidos (24 h)</div>
              <strong>{metrics.events.received_24h}</strong>
              <div className="muted">
                {metrics.events.failed_24h} com falha · {metrics.events.platform_failed} compras da plataforma com falha
              </div>
            </div>
            <div className="card">
              <div className="muted">Vendedor IA (hoje)</div>
              <strong>{metrics.ai.replies_today}</strong>
              <div className="muted">respostas · {metrics.ai.failures_today} falhas</div>
            </div>
            <div className="card">
              <div className="muted">Clientes</div>
              <strong>{metrics.clients.total}</strong>
              <div className="muted">
                {Object.entries(metrics.clients.by_plan_status)
                  .map(([k, v]) => `${v} ${PLAN_STATUS[k] ?? k}`)
                  .join(" · ") || "—"}
                {metrics.clients.deletion_pending > 0 && ` · ${metrics.clients.deletion_pending} com exclusão marcada`}
              </div>
            </div>
          </div>
        </section>
      )}

      <section className="card" aria-labelledby="clients-title" style={{ marginTop: 16 }}>
        <h2 id="clients-title" style={{ marginTop: 0 }}>
          Clientes {clients ? `(${clients.total})` : ""}
        </h2>
        {!clients && <p className="muted">Carregando…</p>}
        {clients && (
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Cliente</th>
                  <th>Plano</th>
                  <th>Assinatura</th>
                  <th>IA no mês</th>
                  <th>Falhas 24 h</th>
                  <th>Ações</th>
                </tr>
              </thead>
              <tbody>
                {clients.items.map((c) => {
                  const suspended = c.plan_status === "suspended";
                  const chosen = plans[c.id] ?? c.plan ?? "";
                  return (
                    <tr key={c.id}>
                      <td>
                        <strong>{c.name}</strong>
                        <div className="muted">{c.owner || "sem dono ainda"}</div>
                        {c.deletion_due_at && (
                          <div className="muted">exclusão em {new Date(c.deletion_due_at).toLocaleDateString("pt-BR")}</div>
                        )}
                      </td>
                      <td>
                        <label className="muted" htmlFor={`plan-${c.id}`}>
                          Plano de {c.name}
                        </label>
                        <select
                          id={`plan-${c.id}`}
                          value={chosen}
                          onChange={(e) => setPlans({ ...plans, [c.id]: e.target.value })}
                        >
                          {clients.plans.map((p) => (
                            <option key={p.key} value={p.key}>
                              {p.key}
                            </option>
                          ))}
                        </select>
                        <button
                          disabled={!chosen || chosen === c.plan}
                          onClick={() =>
                            void act(
                              `/v1/admin/clients/${c.id}/plan`,
                              { method: "PUT", body: JSON.stringify({ plan: chosen }) },
                              `Plano de ${c.name} trocado.`,
                            )
                          }
                        >
                          Trocar plano
                        </button>
                      </td>
                      <td>
                        <span className={`badge ${suspended ? "needs_attention" : "connected"}`}>
                          {c.plan_status ? (PLAN_STATUS[c.plan_status] ?? c.plan_status) : "—"}
                        </span>
                      </td>
                      <td>
                        {c.ai_replies_month}
                        {c.ai_failures_month > 0 && <span className="muted"> ({c.ai_failures_month} falhas)</span>}
                      </td>
                      <td>
                        {c.sends_failed_24h} envios · {c.events_failed_24h} eventos
                      </td>
                      <td>
                        <div className="row" style={{ marginTop: 0 }}>
                          {suspended ? (
                            <button
                              aria-label={`Reativar ${c.name}`}
                              onClick={() =>
                                void act(`/v1/admin/clients/${c.id}/reactivate`, { method: "POST" }, `${c.name} reativado.`)
                              }
                            >
                              Reativar
                            </button>
                          ) : (
                            <button
                              className="danger"
                              aria-label={`Suspender ${c.name}`}
                              onClick={() => {
                                if (window.confirm(`Suspender ${c.name}? Envios e vendedor IA ficam pausados; nada é apagado.`))
                                  void act(`/v1/admin/clients/${c.id}/suspend`, { method: "POST" }, `${c.name} suspenso.`);
                              }}
                            >
                              Suspender
                            </button>
                          )}
                          <button
                            aria-label={`Renovar convite de ${c.name}`}
                            onClick={() =>
                              void act(
                                `/v1/admin/clients/${c.id}/resend-invite`,
                                { method: "POST" },
                                `Convite de ${c.name} renovado (não é enviado e-mail: avise quem comprou).`,
                              )
                            }
                          >
                            Renovar convite
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}

export default function Page() {
  return <Admin />;
}
