"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { api, ApiError, storeTenant, type Me } from "@/lib/api";

const MeContext = createContext<Me | null>(null);

export function useMe(): Me {
  const me = useContext(MeContext);
  if (!me) throw new Error("useMe fora do Shell");
  return me;
}

const NAV = [
  { href: "/", label: "Visão geral" },
  { href: "/onboarding", label: "Primeiros passos" },
  { href: "/inbox", label: "Conversas" },
  { href: "/seller", label: "Vendedor IA" },
  { href: "/recovery", label: "Recuperação" },
  { href: "/reports", label: "Relatórios" },
  { href: "/connections", label: "Conexões" },
  { href: "/plan", label: "Meu plano" },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const path = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Me>("/v1/me")
      .then(setMe)
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 401) router.replace("/login");
        else setError(e instanceof Error ? e.message : "Não foi possível carregar sua conta.");
      });
  }, [router]);

  async function logout() {
    await api("/v1/auth/logout", { method: "POST" }).catch(() => undefined);
    router.replace("/login");
  }

  if (error) {
    return (
      <div className="center">
        <div className="card login">
          <h1>Não foi possível entrar</h1>
          <p className="muted">{error}</p>
          <button onClick={() => router.replace("/login")}>Voltar ao login</button>
        </div>
      </div>
    );
  }
  if (!me) return <div className="center muted">Carregando…</div>;

  const current = me.tenants.find((t) => t.id === me.tenant.id);

  return (
    <MeContext.Provider value={me}>
      <div className="shell">
        <aside className="side">
          <div className="brand">AtendeVendeIA</div>
          <nav className="nav" aria-label="Principal">
            {NAV.map((item) => (
              <Link key={item.href} href={item.href} aria-current={path === item.href ? "page" : undefined}>
                {item.label}
              </Link>
            ))}
          </nav>
        </aside>
        <main className="main">
          <div className="top">
            <div>
              {me.tenants.length > 1 ? (
                <select
                  aria-label="Cliente"
                  value={me.tenant.id}
                  onChange={(e) => {
                    storeTenant(e.target.value);
                    window.location.reload();
                  }}
                >
                  {me.tenants.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              ) : (
                <strong>{current?.name}</strong>
              )}
              <div className="muted">{me.user.email}</div>
            </div>
            <button onClick={logout}>Sair</button>
          </div>
          {me.plan.status === "past_due" && (
            <div className="alert" role="status">
              Seu pagamento está em atraso. <Link href="/plan">Veja o prazo</Link>.
            </div>
          )}
          {["suspended", "canceled", "refunded"].includes(me.plan.status) && (
            <div className="alert bad" role="status">
              Assinatura inativa: envios e vendedor IA pausados. <Link href="/plan">Saiba mais</Link>.
            </div>
          )}
          {children}
        </main>
      </div>
    </MeContext.Provider>
  );
}
