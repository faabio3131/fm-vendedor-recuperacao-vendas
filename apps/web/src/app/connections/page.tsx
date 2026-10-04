"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ProviderCard } from "@/components/ProviderCard";
import { Shell, useMe } from "@/components/Shell";
import { api, type Connection, type Provider } from "@/lib/api";

const GROUPS: { key: string; title: string }[] = [
  { key: "canal", title: "Canais de atendimento" },
  { key: "checkout", title: "Checkout e vendas" },
  { key: "ia", title: "Inteligência artificial" },
  { key: "pagamento", title: "Pagamentos e cobrança" },
  { key: "anuncios", title: "Anúncios" },
];

function Connections() {
  const me = useMe();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([api<Provider[]>("/v1/providers"), api<Connection[]>("/v1/connections")])
      .then(([p, c]) => {
        setProviders(p);
        setConnections(c);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Erro ao carregar."));
  }, []);

  useEffect(load, [load]);

  const canEdit = me.tenant.role === "owner" || me.tenant.role === "admin";

  return (
    <>
      <h1>Conexões</h1>
      <p className="muted">
        Informe aqui as credenciais da sua operação. Elas ficam cifradas e não aparecem de novo depois de salvas.
      </p>
      <p className="muted">
        <Link href="/captures">Ver eventos capturados (diagnóstico da Cakto e da Hotmart)</Link>
      </p>
      {error && <div className="alert bad">{error}</div>}
      {GROUPS.map((g) => {
        const list = providers.filter((p) => p.group === g.key);
        if (list.length === 0) return null;
        return (
          <section key={g.key}>
            <h2>{g.title}</h2>
            <div className="grid">
              {list.map((p) => (
                <ProviderCard
                  key={p.key}
                  provider={p}
                  connection={connections.find((c) => c.provider === p.key)}
                  canEdit={canEdit}
                  onChange={load}
                />
              ))}
            </div>
          </section>
        );
      })}
    </>
  );
}

export default function Page() {
  return (
    <Shell>
      <Connections />
    </Shell>
  );
}
