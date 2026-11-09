"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Shell, useMe } from "@/components/Shell";
import { api, brl, type Offer } from "@/lib/api";

type Account = {
  provider: "asaas"; configured: boolean; verified: boolean;
  mode?: "sandbox" | "production"; delivery_fee_cents?: number;
  webhook_url?: string;
};
type Order = {
  id: string;
  status: "pending" | "charging" | "awaiting_payment" | "paid" | "refunded" | "disputed" | "review" | "canceled";
  customer_name: string; customer_phone: string; customer_email: string;
  items_cents: number; delivery_fee_cents: number; total_cents: number;
  created_at: string; paid_at: string | null; settled_at: string | null;
  items: { name: string; quantity: number; unit_price_cents: number; line_cents: number }[];
  charge: { status: string; provider: string; mode: string; invoice_url: string | null } | null;
};
const LABELS: Record<Order["status"], string> = {
  pending: "Pedido criado", charging: "Cobrança em processamento",
  awaiting_payment: "Aguardando pagamento", paid: "Pagamento confirmado",
  refunded: "Estornado", disputed: "Contestação", review: "Revisão financeira",
  canceled: "Cancelado",
};

function Orders() {
  const me = useMe();
  const canEdit = me.tenant.role === "owner" || me.tenant.role === "admin";
  const [account, setAccount] = useState<Account | null>(null);
  const [offers, setOffers] = useState<Offer[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"sandbox" | "production">("sandbox");
  const [apiKey, setApiKey] = useState("");
  const [webhookToken, setWebhookToken] = useState("");
  const [fee, setFee] = useState("0,00");
  const [customerName, setCustomerName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [selected, setSelected] = useState<Record<string, number>>({});
  const [cpf, setCpf] = useState<Record<string, string>>({});
  const keyRef = useRef("");
  const load = useCallback(() => {
    api<Account>("/v1/merchant/account").then(setAccount).catch(() => undefined);
    api<Order[]>("/v1/merchant/orders").then(setOrders).catch(() => undefined);
    api<Offer[]>("/v1/seller/offers").then(setOffers).catch(() => undefined);
  }, []);
  useEffect(load, [load]);

  async function action(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setFeedback(null);
    try { await work(); load(); }
    catch (err) { setFeedback(err instanceof Error ? err.message : "Operação não concluída."); }
    finally { setBusy(false); }
  }
  function parseMoney(input: string): number | null {
    if (!/^\d{1,6}(,\d{1,2})?$/.test(input.trim())) return null;
    return Math.round(Number(input.trim().replace(",", ".")) * 100);
  }
  async function connect() {
    const delivery = parseMoney(fee);
    if (delivery === null) { setFeedback("Taxa de entrega inválida."); return; }
    await action(async () => {
      await api<Account>("/v1/merchant/account", {
        method: "PUT",
        body: JSON.stringify({
          mode, access_token: apiKey.trim(), webhook_token: webhookToken.trim(),
          delivery_fee_cents: delivery,
        }),
      });
      setApiKey(""); setWebhookToken("");
      setFeedback("Conta verificada. Configure o webhook no painel Asaas antes de cobrar.");
    });
  }
  const items = Object.entries(selected).filter(([, quantity]) => quantity > 0)
    .map(([offer_id, quantity]) => ({ offer_id, quantity }));
  const estimated = items.reduce((total, item) => {
    const offer = offers.find((o) => o.id === item.offer_id);
    return total + (offer?.price_cents ?? 0) * item.quantity;
  }, 0) + (account?.delivery_fee_cents ?? 0);
  async function createOrder() {
    if (!keyRef.current) keyRef.current = crypto.randomUUID();
    await action(async () => {
      await api("/v1/merchant/orders", {
        method: "POST",
        body: JSON.stringify({
          idempotency_key: keyRef.current, customer_name: customerName,
          customer_phone: phone, customer_email: email, items,
        }),
      });
      keyRef.current = "";
      setCustomerName(""); setPhone(""); setEmail(""); setSelected({});
      setFeedback("Pedido criado. Gere sua cobrança individual antes de enviar ao cliente.");
    });
  }
  async function charge(orderId: string) {
    const taxId = cpf[orderId] ?? "";
    await action(async () => {
      const updated = await api<Order>(`/v1/merchant/orders/${orderId}/charge`, {
        method: "POST",
        body: JSON.stringify({ customer_cpf_cnpj: taxId }),
      });
      setCpf((current) => ({ ...current, [orderId]: "" }));
      setFeedback(updated.charge?.invoice_url
        ? "Cobrança criada. Copie o link e envie ao cliente. Ainda não é pagamento confirmado."
        : "Cobrança criada, aguardando confirmação.");
    });
  }
  async function reconcile(orderId: string) {
    await action(async () => {
      const updated = await api<Order>(`/v1/merchant/orders/${orderId}/reconcile`, {
        method: "POST",
      });
      setFeedback("Consulta concluída. Estado atual: " + LABELS[updated.status] + ".");
    });
  }

  return <>
    <h1>Pedidos e pagamentos</h1>
    <p className="muted">O estabelecimento recebe diretamente em sua própria conta Asaas. O AtendeVendeIA não recebe nem movimenta os valores. O estado financeiro só muda após confirmação consultada no provedor.</p>
    {feedback && <div className="alert" role="status">{feedback}</div>}
    <section className="card">
      <h2>1. Conta de recebimento</h2>
      <p className="muted">{account?.verified
        ? `Asaas verificado — ambiente ${account.mode === "production" ? "produção real" : "sandbox de testes"}.`
        : "Ainda não há uma conta Asaas verificada para gerar cobranças."}</p>
      {account?.webhook_url && (
        <label>URL de webhook (cadastrar no Asaas com os eventos de cobrança)
          <input readOnly value={account.webhook_url} />
        </label>
      )}
      <p className="muted">O token de webhook é diferente da chave da API. Cadastre exatamente o mesmo token no Asaas. A confirmação nunca usa comprovantes enviados no WhatsApp.</p>
      {canEdit && (
        <div className="card" style={{ padding: 12 }}>
          <label>Ambiente
            <select value={mode} disabled={busy} onChange={(e) => setMode(e.target.value as "sandbox" | "production")}>
              <option value="sandbox">Sandbox — testes sem dinheiro real</option>
              <option value="production">Produção — cobranças reais</option>
            </select>
          </label>
          <label>Chave de API Asaas
            <input type="password" autoComplete="off" value={apiKey} disabled={busy}
              onChange={(e) => setApiKey(e.target.value)} placeholder="Chave da conta do estabelecimento" />
          </label>
          <label>Token independente do webhook
            <input type="password" autoComplete="off" value={webhookToken} disabled={busy}
              onChange={(e) => setWebhookToken(e.target.value)} placeholder="32 caracteres ou mais" />
          </label>
          <label>Taxa de entrega padrão (R$)
            <input inputMode="decimal" value={fee} disabled={busy}
              onChange={(e) => setFee(e.target.value)} placeholder="0,00" />
          </label>
          <button className="primary" disabled={busy || !apiKey || !webhookToken}
            onClick={() => void connect()}>Verificar e salvar conta de recebimento</button>
        </div>
      )}
    </section>
    <section className="card">
      <h2>2. Novo pedido</h2>
      <p className="muted">O total é calculado novamente no servidor, com o preço real do catálogo. Não há campo para alterar o preço manualmente.</p>
      {account?.verified && canEdit ? (
        <>
          <label>Nome do comprador
            <input value={customerName} disabled={busy} maxLength={120} onChange={(e) => setCustomerName(e.target.value)} />
          </label>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <label>WhatsApp (opcional)
              <input value={phone} disabled={busy} maxLength={25} onChange={(e) => setPhone(e.target.value)} />
            </label>
            <label>E-mail (opcional)
              <input value={email} disabled={busy} maxLength={254} onChange={(e) => setEmail(e.target.value)} />
            </label>
          </div>
          <h3>Produtos</h3>
          {offers.filter((o) => o.active && o.price_cents > 0).map((o) => (
            <div className="row" key={o.id} style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
              <span>{o.name} — {brl(o.price_cents)}</span>
              <label>Quantidade
                <input type="number" min={0} max={99} step={1} disabled={busy}
                  value={selected[o.id] ?? 0}
                  onChange={(e) => setSelected((current) => ({
                    ...current,
                    [o.id]: Math.max(0, Math.min(99, Number(e.target.value) || 0)),
                  }))} />
              </label>
            </div>
          ))}
          <p><strong>Total estimado: {brl(estimated)}</strong> (será conferido no servidor)</p>
          <button className="primary" disabled={busy || !customerName.trim() || items.length === 0}
            onClick={() => void createOrder()}>Criar pedido</button>
        </>
      ) : <p className="muted">Configure uma conta de pagamento verificada para registrar pedidos cobrados pelo aplicativo.</p>}
    </section>
    <section className="card">
      <h2>3. Acompanhamento financeiro</h2>
      <p className="muted">Não entregue o pedido com base apenas em print ou comprovante. Em caso de divergência, cobrança incerta ou contestação, suspenda a liberação e faça a conciliação.</p>
      {orders.length === 0 && <p className="muted">Nenhum pedido registrado.</p>}
      {orders.map((o) => (
        <div className="card" key={o.id} style={{ padding: 12 }}>
          <div className="top">
            <strong>{o.customer_name} — {brl(o.total_cents)}</strong>
            <span>{LABELS[o.status]}</span>
          </div>
          <p className="muted">Pedido {o.id.slice(0, 8)} · {new Date(o.created_at).toLocaleString("pt-BR")}</p>
          <div className="muted">{o.items.map((it) => `${it.quantity}× ${it.name}`).join(" · ")}</div>
          {o.status === "paid" && <p role="status">Pagamento confirmado pelo provedor. {o.settled_at ? "Valor recebido pela conta Asaas." : "Liquidação ainda pendente."}</p>}
          {["disputed", "refunded", "review"].includes(o.status) && <p role="alert">Atenção: revisão financeira obrigatória antes da entrega.</p>}
          {o.charge?.invoice_url && (
            <p>Link individual: <a href={o.charge.invoice_url} target="_blank" rel="noopener noreferrer">Abrir cobrança Asaas</a>{" "}
              <button disabled={busy} onClick={() => void navigator.clipboard.writeText(o.charge?.invoice_url ?? "")}>Copiar link</button>
            </p>
          )}
          {canEdit && o.status === "pending" && !o.charge && (
            <>
              <label>CPF ou CNPJ do comprador para emissão da cobrança (não armazenado)
                <input value={cpf[o.id] ?? ""} autoComplete="off" disabled={busy}
                  onChange={(e) => setCpf((c) => ({ ...c, [o.id]: e.target.value }))} />
              </label>
              <button className="primary" disabled={busy || !cpf[o.id]?.trim()}
                onClick={() => void charge(o.id)}>Gerar cobrança exclusiva</button>
            </>
          )}
          {canEdit && o.charge && (
            <button disabled={busy} onClick={() => void reconcile(o.id)}>
              Consultar pagamento no Asaas
            </button>
          )}
          {o.status === "charging" && <p className="muted">A criação está incerta. Não gere outra cobrança; consulte a conciliação.</p>}
        </div>
      ))}
    </section>
  </>;
}

export default function Page() {
  return <Shell><Orders /></Shell>;
}
