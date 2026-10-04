"use client";

import { useCallback, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { API_URL, api, brl, type RecoveryReport, type ReportRow } from "@/lib/api";

const GROUPS: Record<string, string> = {
  none: "Sem agrupar",
  day: "Por dia",
  product: "Por produto",
  sequence: "Por sequência",
  source: "Por origem",
};
const TRIGGERS: Record<string, string> = {
  abandoned_cart: "Carrinho abandonado",
  pix_pending: "PIX gerado e não pago",
  boleto_pending: "Boleto gerado e não pago",
  purchase_refused: "Pagamento recusado",
  quote_pending: "Orçamento em aberto",
  conversation_cold: "Conversa que esfriou",
};
const SOURCE: Record<string, string> = {
  checkout: "Checkout",
  conversa: "Conversa",
  manual: "Registro manual",
  importacao: "Planilha",
};
const CHANNEL: Record<string, string> = { whatsapp: "WhatsApp", messenger: "Messenger", instagram: "Instagram" };

function pct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(1).replace(".", ",")}%`;
}

function label(group_by: string, g: string): string {
  if (group_by === "sequence") return TRIGGERS[g] ?? g;
  if (group_by === "source") return SOURCE[g] ?? g;
  if (group_by === "day") return new Date(`${g}T12:00:00`).toLocaleDateString("pt-BR");
  return g;
}

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function Reports() {
  const today = new Date();
  const [from, setFrom] = useState(isoDay(new Date(today.getTime() - 29 * 86400000)));
  const [to, setTo] = useState(isoDay(today));
  const [groupBy, setGroupBy] = useState("none");
  const [data, setData] = useState<RecoveryReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const query = `from=${from}&to=${to}&group_by=${groupBy}`;
  const load = useCallback(() => {
    setError(null);
    api<RecoveryReport>(`/v1/reports/recovery?${query}`)
      .then(setData)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Erro ao carregar."));
  }, [query]);
  useEffect(load, [load]);

  const t: ReportRow | undefined = data?.total;
  return (
    <>
      <h1>Relatórios</h1>
      <p className="muted">
        Resultado da recuperação de vendas com o que o sistema registrou. São números do seu negócio, sem projeção: venda
        recuperada é a que aconteceu em até 5 dias depois de uma mensagem enviada, para o mesmo produto.
      </p>
      <div className="card">
        <div className="grid">
          <div>
            <label htmlFor="rep-from">De</label>
            <input id="rep-from" type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div>
            <label htmlFor="rep-to">Até</label>
            <input id="rep-to" type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} />
          </div>
          <div>
            <label htmlFor="rep-group">Agrupar</label>
            <select id="rep-group" value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
              {Object.entries(GROUPS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </div>
        </div>
        <p>
          <a href={`${API_URL}/v1/reports/recovery.csv?${query}`} download>
            Baixar planilha (CSV)
          </a>
        </p>
      </div>
      {error && <div className="alert bad">{error}</div>}
      {t && (
        <>
          <div className="grid" aria-label="Resumo">
            <div className="card">
              <div className="muted">Valor recuperado</div>
              <strong>{brl(t.recovered_cents)}</strong>
              <div className="muted">{t.recovered} venda(s) recuperada(s)</div>
            </div>
            <div className="card">
              <div className="muted">Casos abertos no período</div>
              <strong>{t.cases}</strong>
              <div className="muted">{t.with_message} receberam mensagem</div>
            </div>
            <div className="card">
              <div className="muted">Mensagens enviadas</div>
              <strong>{t.messages_sent}</strong>
              <div className="muted">
                {t.delivered} entregues · {t.read} lidas
              </div>
            </div>
            <div className="card">
              <div className="muted">Responderam</div>
              <strong>{t.replied}</strong>
              <div className="muted">{pct(t.reply_rate)} de quem recebeu mensagem</div>
            </div>
            <div className="card">
              <div className="muted">Taxa de recuperação</div>
              <strong>{pct(t.recovery_rate)}</strong>
              <div className="muted">das pessoas que receberam mensagem</div>
            </div>
            <div className="card">
              <div className="muted">Compraram sem mensagem</div>
              <strong>{t.purchased_without_message}</strong>
              <div className="muted">não contam como recuperadas</div>
            </div>
          </div>
          {data && data.groups.length > 0 && (
            <div className="card tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>{(GROUPS[data.group_by] ?? "").replace("Por ", "")}</th>
                    <th>Casos</th>
                    <th>Com mensagem</th>
                    <th>Enviadas</th>
                    <th>Responderam</th>
                    <th>Recuperadas</th>
                    <th>Valor</th>
                    <th>Taxa</th>
                  </tr>
                </thead>
                <tbody>
                  {data.groups.map((g) => (
                    <tr key={g.group}>
                      <td>{label(data.group_by, g.group)}</td>
                      <td>{g.cases}</td>
                      <td>{g.with_message}</td>
                      <td>{g.messages_sent}</td>
                      <td>{g.replied}</td>
                      <td>{g.recovered}</td>
                      <td>{brl(g.recovered_cents)}</td>
                      <td>{pct(g.recovery_rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {data && data.channels.length > 0 && (
            <div className="card">
              <strong>Conversas por canal no período</strong>
              <ul>
                {data.channels.map((c) => (
                  <li key={c.channel}>
                    {CHANNEL[c.channel] ?? c.channel}: {c.conversations} conversa(s), {c.inbound} recebida(s),{" "}
                    {c.outbound} enviada(s)
                  </li>
                ))}
              </ul>
              <p className="muted">A recuperação por mensagem pronta acontece só no WhatsApp.</p>
            </div>
          )}
        </>
      )}
    </>
  );
}

export default function Page() {
  return (
    <Shell>
      <Reports />
    </Shell>
  );
}
