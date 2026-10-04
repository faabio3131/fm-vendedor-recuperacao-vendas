"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { api, type MyPlan } from "@/lib/api";

const STATUS: Record<string, string> = {
  active: "Ativa",
  past_due: "Em atraso",
  suspended: "Suspensa",
  canceled: "Cancelada",
  refunded: "Reembolsada",
};

function date(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleDateString("pt-BR") : "—";
}

function Plan() {
  const [plan, setPlan] = useState<MyPlan | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<MyPlan>("/v1/plan")
      .then(setPlan)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Erro ao carregar."));
  }, []);

  if (error) return <div className="alert bad">{error}</div>;
  if (!plan) return <div className="muted">Carregando…</div>;
  if (plan.state === "none" || !plan.plan) {
    return (
      <>
        <h1>Meu plano</h1>
        <div className="alert">Sua conta ainda não tem um plano registrado. Fale com o suporte.</div>
      </>
    );
  }
  return (
    <>
      <h1>Meu plano</h1>
      {plan.state === "grace" && (
        <div className="alert" role="status">
          Seu pagamento está em atraso. Tudo continua funcionando até {date(plan.grace_ends_at)}
          {plan.grace_days_left !== null ? ` (${plan.grace_days_left} dia(s))` : ""}. Depois disso, os envios e o vendedor
          IA são pausados, sem apagar nada.
        </div>
      )}
      {plan.state === "blocked" && (
        <div className="alert bad" role="status">
          Sua assinatura está {STATUS[plan.status ?? ""]?.toLowerCase() ?? "inativa"}: os envios de mensagens e o vendedor
          IA estão pausados. Seus dados continuam guardados e voltam a funcionar quando a assinatura for regularizada.
        </div>
      )}
      <div className="card">
        <div className="top">
          <strong>{plan.plan.name}</strong>
          <span className={`badge ${plan.state === "active" ? "connected" : "pending"}`}>
            {STATUS[plan.status ?? ""] ?? plan.status}
          </span>
        </div>
        <p className="muted">
          Contratado em {date(plan.started_at)} · estado desde {date(plan.status_since)}
        </p>
        <p className="muted">
          A cobrança é feita na plataforma onde você comprou ({plan.source === "manual" ? "cadastro manual" : plan.source}
          ). A data da próxima cobrança ainda não é informada pelo sistema.
        </p>
      </div>
      <div className="card">
        <strong>Respostas da IA neste mês</strong>
        <p>
          <strong>{plan.ai.replies}</strong>
          {plan.ai.limit !== null ? ` de ${plan.ai.limit} do seu plano (${plan.ai.percent}%)` : " (sem limite no seu plano)"}
        </p>
        <p className="muted">Limite diário de contatos novos por número: {plan.number_daily_limit}.</p>
      </div>
    </>
  );
}

export default function Page() {
  return (
    <Shell>
      <Plan />
    </Shell>
  );
}
