"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Shell, useMe } from "@/components/Shell";
import { api, type CaptureDetail, type CaptureList } from "@/lib/api";

const AUTH: Record<string, string> = {
  assinatura_hmac: "assinatura HMAC no cabeçalho",
  segredo_no_corpo: "segredo no corpo",
  hottok_cabecalho: "hottok no cabeçalho",
  hottok_corpo: "hottok no corpo",
};

function when(value: string): string {
  return new Date(value).toLocaleString("pt-BR");
}

function Detail({ id }: { id: string }) {
  const [d, setD] = useState<CaptureDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<CaptureDetail>(`/v1/captures/${id}`)
      .then(setD)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Erro."));
  }, [id]);
  if (error) return <div className="alert bad">{error}</div>;
  if (!d) return <div className="muted">Carregando…</div>;
  const c = d.comparison;
  return (
    <div className="card">
      <p className="muted">
        Origem provada por: {d.auth_method ? (AUTH[d.auth_method] ?? d.auth_method) : "não registrado"} · guardado até{" "}
        {when(d.expires_at)}. Dados pessoais aparecem escondidos.
      </p>
      {c.problems.length > 0 ? (
        <div className="alert bad">
          <strong>Problemas</strong>
          <ul>
            {c.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="alert ok">O produto entendeu este evento: todos os campos necessários foram achados.</div>
      )}
      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Campo</th>
              <th>Situação</th>
              <th>Onde</th>
            </tr>
          </thead>
          <tbody>
            {c.fields.map((f) => (
              <tr key={f.field}>
                <td>{f.label}</td>
                <td>{f.status === "ok" ? "achado" : "NÃO achado"}</td>
                <td>{f.found_at ? `${f.found_at} (${f.type})` : `procurado em ${f.tried.join(", ")}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        <strong>Campos que o evento traz e ninguém lê:</strong>{" "}
        {c.extras.length === 0 ? "nenhum" : c.extras.map((e) => `${e.path} (${e.type})`).join(", ")}
      </p>
      <details>
        <summary>Ver o corpo (mascarado)</summary>
        <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{JSON.stringify(d.payload, null, 2)}</pre>
      </details>
    </div>
  );
}

function Captures() {
  const me = useMe();
  const [list, setList] = useState<CaptureList | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const allowed = me.tenant.role === "owner" || me.tenant.role === "admin";

  useEffect(() => {
    if (!allowed) return;
    api<CaptureList>("/v1/captures")
      .then(setList)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Erro ao carregar."));
  }, [allowed]);

  return (
    <>
      <h1>Eventos capturados</h1>
      <p className="muted">
        Diagnóstico: mostra o que a Cakto ou a Hotmart realmente mandou e se o produto entende cada campo.{" "}
        <Link href="/connections">Voltar às conexões</Link>.
      </p>
      {!allowed && <div className="alert">Só dono ou administrador vê esta tela.</div>}
      {error && <div className="alert bad">{error}</div>}
      {list && !list.enabled && (
        <div className="alert" role="status">
          A captura está desligada. Quem opera o sistema liga com <code>FM_CAPTURE_EVENTS=true</code>. Quando ligada, só
          guarda eventos autênticos, sem segredos, e apaga sozinha em {list.ttl_hours} h.
        </div>
      )}
      {list && list.enabled && list.items.length === 0 && (
        <div className="alert">Nenhum evento capturado ainda. Faça uma compra ou um evento de teste na plataforma.</div>
      )}
      {list?.items.map((i) => (
        <div key={i.id}>
          <button
            style={{ width: "100%", textAlign: "left" }}
            aria-expanded={open === i.id}
            onClick={() => setOpen(open === i.id ? null : i.id)}
          >
            <strong>{i.event_type}</strong> <span className="badge">{i.provider}</span>
            <div className="muted">{when(i.captured_at)}</div>
          </button>
          {open === i.id && <Detail id={i.id} />}
        </div>
      ))}
    </>
  );
}

export default function Page() {
  return (
    <Shell>
      <Captures />
    </Shell>
  );
}
