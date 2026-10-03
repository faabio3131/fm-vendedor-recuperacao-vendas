"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Shell, useMe } from "@/components/Shell";
import {
  api,
  brl,
  type ImportResult,
  type RecoveryCase,
  type RecoverySequence,
  type RecoverySettings,
  type RecoverySummary,
  type RecoveryTemplate,
  type Suppression,
} from "@/lib/api";

const TRIGGERS: Record<string, string> = {
  abandoned_cart: "Carrinho abandonado",
  pix_pending: "PIX gerado e não pago",
  boleto_pending: "Boleto gerado e não pago",
  purchase_refused: "Pagamento recusado",
  quote_pending: "Orçamento em aberto",
  conversation_cold: "Conversa que esfriou",
};
const SOURCE: Record<RecoveryCase["source"], string> = {
  checkout: "Checkout",
  conversa: "Conversa",
  manual: "Registro manual",
  importacao: "Planilha",
};
const CASE_STATUS: Record<string, string> = {
  open: "Em andamento",
  recovered: "Recuperada",
  purchased: "Comprou sozinho",
  stopped: "Interrompida",
  exhausted: "Sequência encerrada",
};
const META: Record<RecoveryTemplate["meta_status"], string> = {
  draft: "Rascunho",
  submitted: "Enviado à Meta",
  approved: "Aprovado",
  rejected: "Reprovado",
  paused: "Pausado pela Meta",
  disabled: "Desativado pela Meta",
};
const SKIP: Record<string, string> = {
  template_nao_aprovado: "Template sem aprovação",
  canal_nao_conectado: "WhatsApp não conectado",
  recuperacao_desligada: "Recuperação desligada",
  nao_contatar: "Pediu para não ser contatado",
  maximo_de_contatos: "Máximo de contatos atingido",
  sem_telefone: "Sem telefone",
  limite_do_numero: "Limite diário do número (adiado para o dia seguinte)",
  limite_diario: "Limite diário por contato (adiado)",
};

function useNote() {
  const [note, setNote] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const run = async (action: () => Promise<string | void>) => {
    try {
      const text = await action();
      setNote(text ? { kind: "ok", text } : null);
    } catch (e) {
      setNote({ kind: "bad", text: e instanceof Error ? e.message : "Erro." });
    }
  };
  return { note, run };
}

function Note({ note }: { note: { kind: "ok" | "bad"; text: string } | null }) {
  return note ? (
    <div className={`alert ${note.kind}`} role="status">
      {note.text}
    </div>
  ) : null;
}

function Readiness({ s }: { s: RecoverySummary }) {
  const r = s.readiness;
  const items: [boolean, string, string][] = [
    [r.whatsapp_connected, "WhatsApp oficial conectado", "/connections"],
    [r.approved_templates > 0, "Ao menos um template aprovado na Meta", "#templates"],
    [r.consent_declared, "Consentimento dos contatos declarado", "#ajustes"],
    [r.recovery_enabled, "Recuperação ligada", "#ajustes"],
  ];
  if (items.every(([ok]) => ok)) return null;
  return (
    <div className="alert" aria-label="Para começar a recuperar">
      <strong>Para começar a recuperar vendas, falta:</strong>
      <ul>
        {items
          .filter(([ok]) => !ok)
          .map(([, label, href]) => (
            <li key={label}>{href.startsWith("#") ? <a href={href}>{label}</a> : <Link href={href}>{label}</Link>}</li>
          ))}
      </ul>
    </div>
  );
}

function Summary({ s }: { s: RecoverySummary }) {
  const c = s.cases_by_status;
  return (
    <>
      <h2>Últimos {s.days} dias</h2>
      <div className="grid">
        <div className="card">
          <div className="muted">Recuperado</div>
          <div className="num">{brl(s.recovered_cents)}</div>
          <div className="muted">{c.recovered} vendas recuperadas</div>
        </div>
        <div className="card">
          <div className="muted">Casos abertos</div>
          <div className="num">{s.cases_total}</div>
          <div className="muted">{c.open} em andamento</div>
        </div>
        <div className="card">
          <div className="muted">Mensagens enviadas</div>
          <div className="num">{s.messages_sent}</div>
        </div>
      </div>
      {s.skipped_reasons.length > 0 && (
        <p className="muted">
          Passos não enviados:{" "}
          {s.skipped_reasons.map((x) => `${SKIP[x.reason] ?? x.reason} (${x.count})`).join(", ")}.
        </p>
      )}
    </>
  );
}

function Settings({ canEdit, onSaved }: { canEdit: boolean; onSaved: () => void }) {
  const [s, setS] = useState<RecoverySettings | null>(null);
  const { note, run } = useNote();
  useEffect(() => {
    api<RecoverySettings>("/v1/recovery/settings").then(setS).catch(() => undefined);
  }, []);
  if (!s) return null;
  const save = (patch: Partial<RecoverySettings>) =>
    run(async () => {
      const before = s;
      setS({ ...s, ...patch }); // resposta imediata; volta atrás se o servidor recusar
      try {
        setS(await api<RecoverySettings>("/v1/recovery/settings", { method: "PUT", body: JSON.stringify({ ...s, ...patch }) }));
      } catch (e) {
        setS(before);
        throw e;
      }
      onSaved();
      return "Ajustes salvos.";
    });
  const num = (k: keyof RecoverySettings, v: string) => setS({ ...s, [k]: Number(v) });
  return (
    <section id="ajustes">
      <h2>Ajustes</h2>
      <div className="card">
        <label className="check">
          <input
            type="checkbox"
            disabled={!canEdit}
            checked={s.consent_declared}
            onChange={(e) => void save({ consent_declared: e.target.checked })}
          />
          <span>
            Declaro que meus contatos autorizaram receber mensagens por WhatsApp sobre suas compras e que cada
            mensagem permite pedir para sair (LGPD).
          </span>
        </label>
        <label className="check">
          <input
            type="checkbox"
            disabled={!canEdit || !s.consent_declared}
            checked={s.recovery_enabled}
            onChange={(e) => void save({ recovery_enabled: e.target.checked })}
          />
          <span>Recuperação de vendas ligada</span>
        </label>
        <label className="check">
          <input
            type="checkbox"
            disabled={!canEdit || !s.recovery_enabled}
            checked={s.cold_enabled}
            onChange={(e) => void save({ cold_enabled: e.target.checked })}
          />
          <span>
            Recuperar conversas que esfriaram: quando o cliente pergunta, é respondido e some, o sistema retoma o
            contato.
          </span>
        </label>
        <div className="grid">
          <div>
            <label htmlFor="ch">Conversa esfria depois de (horas sem resposta)</label>
            <input id="ch" type="number" min={1} max={48} disabled={!canEdit} value={s.cold_after_hours} onChange={(e) => num("cold_after_hours", e.target.value)} />
          </div>
          <div>
            <label htmlFor="nl">Contatos novos por dia no seu número (limite da Meta)</label>
            <input id="nl" type="number" min={1} max={100000} disabled={!canEdit} value={s.number_daily_limit} onChange={(e) => num("number_daily_limit", e.target.value)} />
          </div>
          <div>
            <label htmlFor="qs">Não enviar a partir das (hora)</label>
            <input id="qs" type="number" min={0} max={23} disabled={!canEdit} value={s.quiet_start} onChange={(e) => num("quiet_start", e.target.value)} />
          </div>
          <div>
            <label htmlFor="qe">Voltar a enviar às (hora)</label>
            <input id="qe" type="number" min={0} max={23} disabled={!canEdit} value={s.quiet_end} onChange={(e) => num("quiet_end", e.target.value)} />
          </div>
          <div>
            <label htmlFor="dc">Mensagens por contato por dia</label>
            <input id="dc" type="number" min={1} max={10} disabled={!canEdit} value={s.daily_cap} onChange={(e) => num("daily_cap", e.target.value)} />
          </div>
          <div>
            <label htmlFor="mc">Máximo de mensagens por venda</label>
            <input id="mc" type="number" min={1} max={10} disabled={!canEdit} value={s.max_contacts_per_case} onChange={(e) => num("max_contacts_per_case", e.target.value)} />
          </div>
        </div>
        <p className="muted">Fuso horário: {s.timezone}.</p>
        {canEdit && (
          <div className="row">
            <button className="primary" onClick={() => void save({})}>
              Salvar ajustes
            </button>
          </div>
        )}
        <Note note={note} />
      </div>
    </section>
  );
}

function Sequences({ canEdit }: { canEdit: boolean }) {
  const [seqs, setSeqs] = useState<RecoverySequence[]>([]);
  const { note, run } = useNote();
  const load = useCallback(() => {
    api<RecoverySequence[]>("/v1/recovery/sequences").then(setSeqs).catch(() => undefined);
  }, []);
  useEffect(load, [load]);
  const update = (i: number, next: RecoverySequence) => setSeqs(seqs.map((x, j) => (j === i ? next : x)));
  return (
    <section>
      <h2>Sequências</h2>
      <p className="muted">O tempo de cada passo conta a partir do momento do evento.</p>
      {seqs.map((q, i) => (
        <div className="card" key={q.trigger} style={{ marginBottom: 12 }}>
          <div className="top" style={{ marginBottom: 0 }}>
            <strong>{TRIGGERS[q.trigger] ?? q.trigger}</strong>
            {q.is_default && <span className="badge">Padrão</span>}
          </div>
          <label className="check">
            <input type="checkbox" disabled={!canEdit} checked={q.enabled} onChange={(e) => update(i, { ...q, enabled: e.target.checked })} />
            <span>Ativa</span>
          </label>
          {q.steps.map((st, k) => (
            <div className="step" key={k}>
              <div>
                <label htmlFor={`d-${q.trigger}-${k}`}>Minutos depois</label>
                <input
                  id={`d-${q.trigger}-${k}`}
                  type="number"
                  min={1}
                  disabled={!canEdit}
                  value={st.delay_minutes}
                  onChange={(e) =>
                    update(i, { ...q, steps: q.steps.map((x, j) => (j === k ? { ...x, delay_minutes: Number(e.target.value) } : x)) })
                  }
                />
              </div>
              <div>
                <label htmlFor={`t-${q.trigger}-${k}`}>Template</label>
                <input
                  id={`t-${q.trigger}-${k}`}
                  disabled={!canEdit}
                  value={st.template_key}
                  onChange={(e) =>
                    update(i, { ...q, steps: q.steps.map((x, j) => (j === k ? { ...x, template_key: e.target.value } : x)) })
                  }
                />
              </div>
              {canEdit && q.steps.length > 1 && (
                <button className="danger" onClick={() => update(i, { ...q, steps: q.steps.filter((_, j) => j !== k) })} aria-label={`Remover passo ${k + 1}`}>
                  Remover
                </button>
              )}
            </div>
          ))}
          {canEdit && (
            <div className="row">
              <button
                onClick={() =>
                  update(i, {
                    ...q,
                    steps: [...q.steps, { delay_minutes: (q.steps.at(-1)?.delay_minutes ?? 0) + 60, template_key: "" }],
                  })
                }
                disabled={q.steps.length >= 10}
              >
                Adicionar passo
              </button>
              <button
                className="primary"
                onClick={() =>
                  void run(async () => {
                    await api(`/v1/recovery/sequences/${q.trigger}`, { method: "PUT", body: JSON.stringify({ enabled: q.enabled, steps: q.steps }) });
                    load();
                    return `Sequência "${TRIGGERS[q.trigger]}" salva.`;
                  })
                }
              >
                Salvar sequência
              </button>
              {!q.is_default && (
                <button
                  onClick={() =>
                    void run(async () => {
                      await api(`/v1/recovery/sequences/${q.trigger}`, { method: "DELETE" });
                      load();
                      return "Voltou ao padrão.";
                    })
                  }
                >
                  Voltar ao padrão
                </button>
              )}
            </div>
          )}
        </div>
      ))}
      <Note note={note} />
    </section>
  );
}

function Templates({ canEdit, onChange }: { canEdit: boolean; onChange: () => void }) {
  const [tpls, setTpls] = useState<RecoveryTemplate[]>([]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [category, setCategory] = useState<Record<string, string>>({});
  const { note, run } = useNote();
  const load = useCallback(() => {
    api<RecoveryTemplate[]>("/v1/recovery/templates").then(setTpls).catch(() => undefined);
  }, []);
  useEffect(load, [load]);
  return (
    <section id="templates">
      <h2>Mensagens (templates)</h2>
      <div className="alert">
        No WhatsApp, a primeira mensagem para um cliente só pode ser um template aprovado pela Meta. Salve o texto,
        envie para aprovação aqui mesmo (o WhatsApp precisa estar conectado) e use “Atualizar status” para ver a
        resposta da Meta. Nada é enviado sem aprovação. Variáveis: <code>{"{nome}"}</code> <code>{"{produto}"}</code>{" "}
        <code>{"{valor}"}</code> <code>{"{link}"}</code>.
      </div>
      {canEdit && (
        <div className="row" style={{ marginBottom: 12 }}>
          <button
            onClick={() =>
              void run(async () => {
                const r = await api<{ checked: number; updated: number }>("/v1/recovery/templates/sync", { method: "POST" });
                load();
                onChange();
                return `Status consultado na Meta: ${r.checked} template(s), ${r.updated} mudança(s).`;
              })
            }
          >
            Atualizar status
          </button>
        </div>
      )}
      {tpls.map((t) => {
        const body = draft[t.key] ?? t.body;
        return (
          <div className="card" key={t.key} style={{ marginBottom: 12 }}>
            <div className="top" style={{ marginBottom: 0 }}>
              <strong>{t.key}</strong>
              <span className={`badge ${t.meta_status}`}>{META[t.meta_status]}</span>
            </div>
            {t.meta_name && (
              <p className="muted" style={{ margin: "4px 0" }}>
                Nome na Meta: <code>{t.meta_name}</code>
                {t.meta_category ? ` · ${t.meta_category === "MARKETING" ? "Marketing" : "Utilidade"}` : ""}
              </p>
            )}
            {t.meta_reason && (
              <p role="alert" style={{ margin: "4px 0", color: "var(--bad)" }}>
                Motivo informado pela Meta: {t.meta_reason}
              </p>
            )}
            <label htmlFor={`b-${t.key}`}>Texto</label>
            <textarea id={`b-${t.key}`} disabled={!canEdit} value={body} maxLength={1024} onChange={(e) => setDraft({ ...draft, [t.key]: e.target.value })} />
            {canEdit && (
              <div className="row">
                <button
                  className="primary"
                  onClick={() =>
                    void run(async () => {
                      await api(`/v1/recovery/templates/${t.key}`, { method: "PUT", body: JSON.stringify({ body }) });
                      setDraft((d) => Object.fromEntries(Object.entries(d).filter(([k]) => k !== t.key)));
                      load();
                      onChange();
                      return `Texto de ${t.key} salvo.`;
                    })
                  }
                >
                  Salvar texto
                </button>
                {t.saved && (t.meta_status === "draft" || t.meta_status === "rejected") && (
                  <>
                    <label htmlFor={`c-${t.key}`} style={{ margin: 0 }}>
                      Categoria
                    </label>
                    <select
                      id={`c-${t.key}`}
                      style={{ width: "auto" }}
                      value={category[t.key] ?? t.meta_category ?? "MARKETING"}
                      onChange={(e) => setCategory({ ...category, [t.key]: e.target.value })}
                    >
                      <option value="MARKETING">Marketing</option>
                      <option value="UTILITY">Utilidade</option>
                    </select>
                    <button
                      className="primary"
                      onClick={() =>
                        void run(async () => {
                          const r = await api<RecoveryTemplate>(`/v1/recovery/templates/${t.key}/submit`, {
                            method: "POST",
                            body: JSON.stringify({ category: category[t.key] ?? t.meta_category ?? "MARKETING" }),
                          });
                          load();
                          onChange();
                          return r.note === "pending_confirmation"
                            ? `${t.key}: não deu para confirmar o envio. Use “Atualizar status” em instantes.`
                            : `${t.key}: ${META[r.meta_status]}.`;
                        })
                      }
                    >
                      Enviar para aprovação na Meta
                    </button>
                  </>
                )}
                {t.saved &&
                  (["submitted", "approved", "rejected"] as const).map((st) => (
                    <button
                      key={st}
                      disabled={t.meta_status === st}
                      onClick={() =>
                        void run(async () => {
                          await api(`/v1/recovery/templates/${t.key}/status`, { method: "POST", body: JSON.stringify({ status: st }) });
                          load();
                          onChange();
                          return `${t.key}: ${META[st]}.`;
                        })
                      }
                    >
                      Marcar: {META[st]}
                    </button>
                  ))}
              </div>
            )}
          </div>
        );
      })}
      <Note note={note} />
    </section>
  );
}

function Blocklist({ canEdit }: { canEdit: boolean }) {
  const [list, setList] = useState<Suppression[]>([]);
  const [identity, setIdentity] = useState("");
  const { note, run } = useNote();
  const load = useCallback(() => {
    api<Suppression[]>("/v1/recovery/suppressions").then(setList).catch(() => undefined);
  }, []);
  useEffect(load, [load]);
  return (
    <section>
      <h2>Não contatar</h2>
      <div className="card">
        <p className="muted">Quem pediu para não receber mensagens. Os cadastros não podem ser desfeitos por aqui.</p>
        {canEdit && (
          <>
            <label htmlFor="sup">Telefone com DDD ou e-mail</label>
            <input id="sup" value={identity} onChange={(e) => setIdentity(e.target.value)} />
            <div className="row">
              <button
                onClick={() =>
                  void run(async () => {
                    await api("/v1/recovery/suppressions", { method: "POST", body: JSON.stringify({ identity }) });
                    setIdentity("");
                    load();
                    return "Contato bloqueado.";
                  })
                }
              >
                Adicionar
              </button>
            </div>
          </>
        )}
        <Note note={note} />
        <ul>
          {list.map((x) => (
            <li key={x.id}>{x.identity}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}

const toCents = (text: string): number | null => {
  const clean = text.replace(/R\$|\s/g, "");
  if (!clean) return 0;
  const n = Number(clean.includes(",") ? clean.replace(/\./g, "").replace(",", ".") : clean);
  return Number.isFinite(n) && n >= 0 ? Math.round(n * 100) : null;
};

const EMPTY = { name: "", phone: "", product: "", amount: "", link: "", note: "", authorized: false };

function Opportunities({ onChange }: { onChange: () => void }) {
  const [f, setF] = useState(EMPTY);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [fileAuthorized, setFileAuthorized] = useState(false);
  const { note, run } = useNote();
  const set = (k: keyof typeof EMPTY, v: string | boolean) => setF({ ...f, [k]: v });
  const create = () =>
    run(async () => {
      const cents = toCents(f.amount);
      if (cents === null) throw new Error("Valor inválido. Exemplo: 1.234,56");
      await api("/v1/recovery/opportunities", {
        method: "POST",
        body: JSON.stringify({
          name: f.name,
          phone: f.phone,
          product: f.product,
          amount_cents: cents,
          payment_url: f.link || null,
          note: f.note,
          contact_authorized: f.authorized,
        }),
      });
      setF(EMPTY);
      onChange();
      return "Oportunidade registrada. A recuperação começa no horário permitido.";
    });
  const upload = (file: File | undefined) =>
    run(async () => {
      if (!file) return;
      const csv = await file.text();
      const res = await api<ImportResult>("/v1/recovery/opportunities/import", {
        method: "POST",
        body: JSON.stringify({ csv, contact_authorized: fileAuthorized }),
      });
      setResult(res);
      onChange();
      return `${res.created} de ${res.total} linhas registradas.`;
    });
  return (
    <section id="oportunidades">
      <h2>Registrar oportunidade</h2>
      <p className="muted">
        Orçamento enviado, pedido pendente ou cliente que ficou de voltar: registre aqui e o sistema faz o
        acompanhamento pelo WhatsApp.
      </p>
      <div className="card">
        <div className="grid">
          <div>
            <label htmlFor="on">Nome</label>
            <input id="on" value={f.name} maxLength={200} onChange={(e) => set("name", e.target.value)} />
          </div>
          <div>
            <label htmlFor="op">WhatsApp (com DDD)</label>
            <input id="op" inputMode="tel" value={f.phone} onChange={(e) => set("phone", e.target.value)} />
          </div>
          <div>
            <label htmlFor="opr">Produto ou serviço</label>
            <input id="opr" value={f.product} maxLength={200} onChange={(e) => set("product", e.target.value)} />
          </div>
          <div>
            <label htmlFor="ov">Valor (R$)</label>
            <input id="ov" inputMode="decimal" value={f.amount} onChange={(e) => set("amount", e.target.value)} />
          </div>
          <div>
            <label htmlFor="ol">Link de pagamento (opcional)</label>
            <input id="ol" value={f.link} placeholder="https://" onChange={(e) => set("link", e.target.value)} />
          </div>
          <div>
            <label htmlFor="oo">Observação (opcional)</label>
            <input id="oo" value={f.note} maxLength={300} onChange={(e) => set("note", e.target.value)} />
          </div>
        </div>
        <label className="check">
          <input type="checkbox" checked={f.authorized} onChange={(e) => set("authorized", e.target.checked)} />
          <span>Este cliente autorizou receber mensagens por WhatsApp.</span>
        </label>
        <div className="row">
          <button className="primary" onClick={() => void create()}>
            Registrar
          </button>
        </div>
        <hr />
        <h3>Importar planilha</h3>
        <p className="muted">
          Arquivo CSV com cabeçalho. Colunas: <code>telefone</code> (obrigatória), <code>nome</code>,{" "}
          <code>produto</code>, <code>valor</code>, <code>link</code>, <code>observacao</code>. Até 200 linhas.
        </p>
        <label className="check">
          <input type="checkbox" checked={fileAuthorized} onChange={(e) => setFileAuthorized(e.target.checked)} />
          <span>Todos os clientes desta planilha autorizaram receber mensagens por WhatsApp.</span>
        </label>
        <input
          type="file"
          accept=".csv,text/csv,text/plain"
          aria-label="Planilha CSV"
          onChange={(e) => {
            void upload(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        {result && (
          <div className="muted" aria-live="polite">
            {result.skipped.map((x) => (
              <div key={x.reason}>
                {x.count} ignoradas: {x.message}
              </div>
            ))}
            {result.errors.map((x) => (
              <div key={x.line}>
                Linha {x.line}: {x.message}
              </div>
            ))}
            {result.errors_total > result.errors.length && <div>…e mais {result.errors_total - result.errors.length} com erro.</div>}
          </div>
        )}
        <Note note={note} />
      </div>
    </section>
  );
}

function Cases({ version, onChange }: { version: number; onChange: () => void }) {
  const [cases, setCases] = useState<RecoveryCase[]>([]);
  const { note, run } = useNote();
  useEffect(() => {
    api<RecoveryCase[]>("/v1/recovery/cases?limit=30").then(setCases).catch(() => undefined);
  }, [version]);
  const outcome = (id: string, value: "sold" | "lost") =>
    run(async () => {
      await api(`/v1/recovery/cases/${id}/outcome`, { method: "POST", body: JSON.stringify({ outcome: value }) });
      onChange();
      return value === "sold" ? "Venda registrada." : "Marcada como perdida.";
    });
  return (
    <section>
      <h2>Vendas em recuperação</h2>
      <Note note={note} />
      {cases.length === 0 ? (
        <p className="muted">Nenhuma ainda. Registre uma oportunidade acima ou ligue a recuperação de conversas.</p>
      ) : (
        <div className="card tablewrap">
          <table>
            <thead>
              <tr>
                <th>Contato</th>
                <th>Produto</th>
                <th>Origem</th>
                <th>Valor</th>
                <th>Mensagens</th>
                <th>Situação</th>
                <th>Desfecho</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c) => (
                <tr key={c.id}>
                  <td>
                    {c.contact.name || "—"} {c.contact.phone}
                  </td>
                  <td>{c.product || "—"}</td>
                  <td title={TRIGGERS[c.trigger] ?? c.trigger}>{SOURCE[c.source] ?? c.source}</td>
                  <td>{brl(c.amount_cents)}</td>
                  <td>{c.messages_sent}</td>
                  <td>
                    <span className={`badge ${c.status}`}>{CASE_STATUS[c.status] ?? c.status}</span>
                  </td>
                  <td>
                    {(c.status === "open" || c.status === "exhausted") && (
                      <div className="row">
                        <button onClick={() => void outcome(c.id, "sold")}>Vendido</button>
                        <button onClick={() => void outcome(c.id, "lost")}>Perdi</button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Recovery() {
  const me = useMe();
  const canEdit = me.tenant.role === "owner" || me.tenant.role === "admin";
  const [summary, setSummary] = useState<RecoverySummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const loadSummary = useCallback(() => {
    api<RecoverySummary>("/v1/recovery/summary").then(setSummary).catch((e: unknown) => setError(e instanceof Error ? e.message : "Erro."));
  }, []);
  const refresh = useCallback(() => {
    loadSummary();
    setVersion((v) => v + 1);
  }, [loadSummary]);
  useEffect(loadSummary, [loadSummary]);

  if (error) {
    return (
      <>
        <h1>Recuperação</h1>
        <div className="alert bad">{error}</div>
      </>
    );
  }
  return (
    <>
      <h1>Recuperação de vendas</h1>
      <p className="muted">
        Orçamentos em aberto, conversas que esfriaram e vendas paradas no checkout viram mensagens no WhatsApp.
      </p>
      {summary && <Readiness s={summary} />}
      {summary && <Summary s={summary} />}
      <Opportunities onChange={refresh} />
      <Settings canEdit={canEdit} onSaved={loadSummary} />
      <Sequences canEdit={canEdit} />
      <Templates canEdit={canEdit} onChange={loadSummary} />
      <Blocklist canEdit={canEdit} />
      <Cases version={version} onChange={refresh} />
    </>
  );
}

export default function Page() {
  return (
    <Shell>
      <Recovery />
    </Shell>
  );
}
