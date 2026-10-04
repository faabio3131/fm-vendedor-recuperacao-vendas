"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { api, type Onboarding } from "@/lib/api";

const BLOCKS: Record<string, string> = {
  recuperacao: "ligar a recuperação",
  ia: "ligar o vendedor IA",
};

function Steps() {
  const [data, setData] = useState<Onboarding | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Onboarding>("/v1/onboarding")
      .then(setData)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Erro ao carregar."));
  }, []);

  if (error) return <div className="alert bad">{error}</div>;
  if (!data) return <div className="muted">Carregando…</div>;

  return (
    <>
      <h1>Primeiros passos</h1>
      <p className="muted">
        O que falta para atender e vender sem ajuda. A lista olha o estado real da sua conta, então
        se atualiza sozinha.
      </p>

      <div className="card">
        <strong>
          {data.done} de {data.total} passos prontos
        </strong>
        <div
          className="progress"
          role="progressbar"
          aria-label="Progresso dos primeiros passos"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={data.percent}
        >
          <span style={{ width: `${data.percent}%` }} />
        </div>
        <div className="muted">
          {data.can_enable.recuperacao.allowed
            ? "Recuperação: pronta para ligar."
            : "Recuperação: ainda falta preparar."}{" "}
          {data.can_enable.ia.allowed
            ? "Vendedor IA: pronto para ligar."
            : "Vendedor IA: ainda falta preparar."}
        </div>
      </div>

      <ol className="steps" aria-label="Lista de passos">
        {data.steps.map((s) => (
          <li key={s.key}>
            <span className={`mark ${s.done ? "done" : ""}`} aria-hidden="true">
              {s.done ? "✓" : ""}
            </span>
            <div className="body">
              <strong>{s.title}</strong>{" "}
              <span className={`badge ${s.done ? "connected" : "pending"}`}>
                {s.done ? "Pronto" : "Falta"}
              </span>
              <div className="muted">{s.why}</div>
              {!s.done && (
                <>
                  <div>{s.todo}</div>
                  {s.blocks.length > 0 && (
                    <div className="muted">
                      Sem isso não dá para {s.blocks.map((b) => BLOCKS[b] ?? b).join(" nem ")}.
                    </div>
                  )}
                  <div className="row">
                    <Link className="btn" href={s.href}>
                      {s.action}
                    </Link>
                  </div>
                </>
              )}
            </div>
          </li>
        ))}
      </ol>
    </>
  );
}

export default function Page() {
  return (
    <Shell>
      <Steps />
    </Shell>
  );
}
