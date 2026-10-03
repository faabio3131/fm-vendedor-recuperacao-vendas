"use client";

import { useCallback, useEffect, useState } from "react";
import { Shell, useMe } from "@/components/Shell";
import { api, brl, type AiUsage, type Offer, type SellerSettings } from "@/lib/api";

type Draft = { name: string; description: string; price: string; payment_url: string };
const EMPTY: Draft = { name: "", description: "", price: "", payment_url: "https://" };

function toCents(price: string): number {
  return Math.round(Number(price.replace(/\./g, "").replace(",", ".")) * 100);
}

function Seller() {
  const me = useMe();
  const canEdit = me.tenant.role === "owner" || me.tenant.role === "admin";
  const [offers, setOffers] = useState<Offer[]>([]);
  const [settings, setSettings] = useState<SellerSettings | null>(null);
  const [usage, setUsage] = useState<AiUsage | null>(null);
  const [persona, setPersona] = useState("");
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [note, setNote] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);

  const load = useCallback(() => {
    api<Offer[]>("/v1/seller/offers").then(setOffers).catch(() => undefined);
    api<AiUsage>("/v1/seller/usage").then(setUsage).catch(() => undefined);
    api<SellerSettings>("/v1/seller/settings")
      .then((s) => {
        setSettings(s);
        setPersona(s.ai_persona);
      })
      .catch(() => undefined);
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

  const saveSettings = (ai_enabled: boolean) =>
    run(async () => {
      await api("/v1/seller/settings", { method: "PUT", body: JSON.stringify({ ai_enabled, ai_persona: persona }) });
      return "Ajustes salvos.";
    });

  const addOffer = () =>
    run(async () => {
      const cents = toCents(draft.price);
      if (!Number.isFinite(cents)) throw new Error("Informe o preço, por exemplo 197,00.");
      await api("/v1/seller/offers", {
        method: "POST",
        body: JSON.stringify({ name: draft.name, description: draft.description, price_cents: cents, payment_url: draft.payment_url }),
      });
      setDraft(EMPTY);
      return "Oferta cadastrada.";
    });

  const toggle = (o: Offer) =>
    run(async () => {
      await api(`/v1/seller/offers/${o.id}`, { method: "PUT", body: JSON.stringify({ ...o, active: !o.active }) });
      return o.active ? "Oferta desativada." : "Oferta ativada.";
    });

  const remove = (o: Offer) =>
    run(async () => {
      if (!window.confirm(`Remover a oferta "${o.name}"?`)) return "Nada foi removido.";
      await api(`/v1/seller/offers/${o.id}`, { method: "DELETE" });
      return "Oferta removida.";
    });

  return (
    <>
      <h1>Vendedor IA</h1>
      <p className="muted">O vendedor só fala do que está cadastrado aqui. Preço e link de pagamento vêm sempre desta lista.</p>
      {note && (
        <div className={`alert ${note.kind}`} role="status">
          {note.text}
        </div>
      )}

      <h2>Ajustes</h2>
      <div className="card">
        {settings && !settings.ai_available && (
          <div className="alert">
            O modelo de IA ainda não está habilitado nesta instalação: com o vendedor ligado, as conversas vão
            para uma pessoa responder.
          </div>
        )}
        <label className="check">
          <input
            type="checkbox"
            disabled={!canEdit || !settings}
            checked={settings?.ai_enabled ?? false}
            onChange={(e) => void saveSettings(e.target.checked)}
          />
          <span>Vendedor IA responde as mensagens recebidas</span>
        </label>
        {usage && (
          <p className="muted" aria-label="Uso da IA no mês">
            Respostas da IA neste mês: <strong>{usage.replies}</strong>
            {usage.limit !== null ? ` de ${usage.limit} do seu plano (${usage.percent}%)` : " (sem limite no seu plano)"}.
            {usage.limit !== null && usage.replies >= usage.limit &&
              " O limite foi atingido: as novas conversas vão para uma pessoa até o próximo mês."}
            {usage.limit !== null && usage.replies < usage.limit && (usage.percent ?? 0) >= 80 &&
              " Está perto do limite."}
          </p>
        )}
        {settings && settings.ai_enabled && settings.active_offers === 0 && (
          <div className="alert">Sem oferta ativa, toda conversa vai para uma pessoa. Cadastre uma abaixo.</div>
        )}
        <label htmlFor="persona">Tom de voz (opcional)</label>
        <textarea id="persona" maxLength={600} disabled={!canEdit} value={persona} onChange={(e) => setPersona(e.target.value)} />
        {canEdit && (
          <div className="row">
            <button className="primary" onClick={() => void saveSettings(settings?.ai_enabled ?? false)}>
              Salvar ajustes
            </button>
          </div>
        )}
      </div>

      <h2>Ofertas</h2>
      {offers.length === 0 && <p className="muted">Nenhuma oferta ainda.</p>}
      {offers.map((o) => (
        <div className="card" key={o.id} style={{ marginBottom: 12 }}>
          <div className="top" style={{ marginBottom: 0 }}>
            <strong>{o.name}</strong>
            <span className={`badge ${o.active ? "connected" : "locked"}`}>{o.active ? "Ativa" : "Inativa"}</span>
          </div>
          <div>{brl(o.price_cents)}</div>
          <div className="muted">{o.description}</div>
          <code>{o.payment_url}</code>
          {canEdit && (
            <div className="row">
              <button onClick={() => void toggle(o)}>{o.active ? "Desativar" : "Ativar"}</button>
              <button className="danger" onClick={() => void remove(o)}>
                Remover
              </button>
            </div>
          )}
        </div>
      ))}

      {canEdit && (
        <div className="card">
          <strong>Nova oferta</strong>
          <label htmlFor="o-name">Nome</label>
          <input id="o-name" value={draft.name} maxLength={120} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          <label htmlFor="o-desc">Descrição</label>
          <textarea id="o-desc" maxLength={1000} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
          <label htmlFor="o-price">Preço (R$)</label>
          <input id="o-price" inputMode="decimal" value={draft.price} onChange={(e) => setDraft({ ...draft, price: e.target.value })} />
          <label htmlFor="o-url">Link de pagamento (https)</label>
          <input id="o-url" value={draft.payment_url} onChange={(e) => setDraft({ ...draft, payment_url: e.target.value })} />
          <div className="row">
            <button className="primary" onClick={() => void addOffer()}>
              Cadastrar oferta
            </button>
          </div>
        </div>
      )}
    </>
  );
}

export default function Page() {
  return (
    <Shell>
      <Seller />
    </Shell>
  );
}
