"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { api, ApiError, storeTenant, type Me } from "@/lib/api";
import { Brand } from "@/components/Brand";

const MeContext = createContext<Me | null>(null);

export function useMe(): Me {
  const me = useContext(MeContext);
  if (!me) throw new Error("useMe fora do Shell");
  return me;
}

const ICONS: Record<string, string> = {"/": "M3 11l9-8 9 8M5 10v10h14V10", "/onboarding": "M5 21V4m0 0h12l-2 4 2 4H5", "/inbox": "M4 5h16v11H9l-5 4z", "/seller": "M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2z", "/recovery": "M4 12a8 8 0 1 0 3-6.2M4 4v4h4", "/reports": "M5 20V10m5 10V4m5 16v-7m5 7H3", "/connections": "M9 8H7a4 4 0 0 0 0 8h2m6-8h2a4 4 0 0 1 0 8h-2M8 12h8", "/plan": "M3 7h18v12H3zM3 11h18", "/install": "M8 3h8v18H8zM11 18h2", "/privacy": "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"};

function NavIcon({ href }: { href: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={ICONS[href]} />
    </svg>
  );
}

const NAV: { href: string; label: string; roles?: string[] }[] = [
  { href: "/", label: "Visão geral" },
  { href: "/onboarding", label: "Primeiros passos" },
  { href: "/inbox", label: "Conversas" },
  { href: "/seller", label: "Vendedor IA" },
  { href: "/recovery", label: "Recuperação" },
  { href: "/reports", label: "Relatórios" },
  { href: "/connections", label: "Conexões" },
  { href: "/plan", label: "Meu plano" },
  { href: "/install", label: "App no celular" },
  { href: "/privacy", label: "Privacidade", roles: ["owner", "admin"] },
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
        if (e instanceof ApiError && e.status === 401) {
          router.replace("/login");
          return;
        }
        const message = e instanceof Error ? e.message : "Não foi possível carregar sua conta.";
        if (e instanceof ApiError && e.status === 403) {
          // Quem é só da equipe da plataforma não tem cliente: manda para a administração.
          api("/v1/admin/me")
            .then(() => router.replace("/admin"))
            .catch(() => setError(message));
          return;
        }
        setError(message);
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
          <Brand size={34} />
          <nav className="nav" aria-label="Principal">
            {NAV.filter((item) => !item.roles || item.roles.includes(me.tenant.role)).map((item) => (
              <Link key={item.href} href={item.href} aria-current={path === item.href ? "page" : undefined}>
                <NavIcon href={item.href} />
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
