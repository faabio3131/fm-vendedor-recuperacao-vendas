"use client";

import { useCallback, useEffect, useState } from "react";
import { Shell, useMe } from "@/components/Shell";
import { api, ApiError, type EraseResult, type Privacy } from "@/lib/api";

const ACTIONS: Record<string, string> = {
  declared: "Declarou o consentimento dos contatos",
  revoked: "Retirou a declaração de consentimento",
  registered: "Confirmou a autorização de contatos",
};
const ORIGINS: Record<string, string> = {
  painel: "Painel",
  registro_avulso: "Registro avulso",
  importacao: "Planilha",
};

function when(value: string): string {
  return new Date(value).toLocaleString("pt-BR");
}

function message(e: unknown): string {
  return e instanceof ApiError || e instanceof Error ? e.message : "Algo deu errado.";
}

function Page_() {
  const me = useMe();
  const isOwner = me.tenant.role === "owner";
  const [data, setData] = useState<Privacy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState("");
  const [note, setNote] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const [who, setWho] = useState("");
  const [confirmErase, setConfirmErase] = useState(false);
  const [alsoBlock, setAlsoBlock] = useState(false);
  const [contactNote, setContactNote] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const [typed, setTyped] = useState("");
  const [deletionNote, setDeletionNote] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Privacy>("/v1/privacy")
      .then((p) => {
        setData(p);
        setDays(String(p.retention_days));
      })
      .catch((e: unknown) => setError(message(e)));
  }, []);
  useEffect(load, [load]);

  async function saveRetention() {
    setNote(null);
    try {
      await api("/v1/privacy/retention", { method: "PUT", body: JSON.stringify({ retention_days: Number(days) }) });
      setNote({ kind: "ok", text: "Prazo de retenção salvo." });
      load();
    } catch (e) {
      setNote({ kind: "bad", text: message(e) });
    }
  }

  async function exportContact() {
    setContactNote(null);
    try {
      const res = await api<{ found: boolean; data?: unknown }>("/v1/privacy/contacts/export", {
        method: "POST",
        body: JSON.stringify({ identifier: who }),
      });
      if (!res.found) {
        setContactNote({ kind: "bad", text: "Nenhum contato encontrado com esse dado." });
        return;
      }
      const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "dados-do-contato.json";
      a.click();
      URL.revokeObjectURL(url);
      setContactNote({ kind: "ok", text: "Arquivo gerado. Ele tem dados pessoais: guarde e envie com cuidado." });
    } catch (e) {
      setContactNote({ kind: "bad", text: message(e) });
    }
  }

  async function eraseContact() {
    setContactNote(null);
    try {
      const res = await api<EraseResult>("/v1/privacy/contacts/erase", {
        method: "POST",
        body: JSON.stringify({ identifier: who, confirm: confirmErase, also_block: alsoBlock }),
      });
      if (!res.found) {
        setContactNote({ kind: "bad", text: "Nenhum contato encontrado (ou já foi apagado)." });
        return;
      }
      const e = res.erased;
      setContactNote({
        kind: "ok",
        text: `Contato apagado: ${e?.conversas ?? 0} conversa(s), ${e?.mensagens ?? 0} mensagem(ns) e ${e?.casos ?? 0} caso(s).${
          res.block_kept ? " O pedido de não contatar foi mantido." : ""
        }`,
      });
      setWho("");
      setConfirmErase(false);
      setAlsoBlock(false);
    } catch (e) {
      setContactNote({ kind: "bad", text: message(e) });
    }
  }

  async function requestDeletion() {
    setDeletionNote(null);
    try {
      await api("/v1/privacy/account/deletion", { method: "POST", body: JSON.stringify({ confirm: typed }) });
      setTyped("");
      load();
    } catch (e) {
      setDeletionNote(message(e));
    }
  }

  async function cancelDeletion() {
    setDeletionNote(null);
    try {
      await api("/v1/privacy/account/deletion", { method: "DELETE" });
      load();
    } catch (e) {
      setDeletionNote(message(e));
    }
  }

  if (error) return <div className="alert bad">{error}</div>;
  if (!data) return <div className="muted">Carregando…</div>;

  return (
    <>
      <h1>Privacidade</h1>
      <p className="muted">
        Seus clientes e contatos têm direitos sobre os dados deles. Aqui você exporta, apaga e controla por quanto
        tempo guardamos.
      </p>

      <section className="card" aria-labelledby="ret-title" style={{ marginTop: 16 }}>
        <h2 id="ret-title" style={{ marginTop: 0 }}>
          Quanto tempo guardar as conversas
        </h2>
        <p className="muted">
          Conversas, casos encerrados e eventos recebidos mais antigos que isso são apagados sozinhos. Casos em andamento
          nunca são apagados. O padrão de {data.retention_default} dias é provisório.
        </p>
        <label htmlFor="ret-days">Dias (de {data.retention_min} a {data.retention_max})</label>
        <input
          id="ret-days"
          type="number"
          inputMode="numeric"
          min={data.retention_min}
          max={data.retention_max}
          value={days}
          onChange={(e) => setDays(e.target.value)}
        />
        <div className="row">
          <button className="primary" onClick={() => void saveRetention()}>
            Salvar prazo
          </button>
        </div>
        {note && (
          <div className={`alert ${note.kind}`} role="status">
            {note.text}
          </div>
        )}
      </section>

      <section className="card" aria-labelledby="who-title" style={{ marginTop: 16 }}>
        <h2 id="who-title" style={{ marginTop: 0 }}>
          Dados de uma pessoa
        </h2>
        <p className="muted">
          Quando alguém pede os dados dela ou pede para ser esquecida. Busque pelo telefone, e-mail ou ID do canal
          (por exemplo, messenger:123).
        </p>
        <label htmlFor="who">Telefone, e-mail ou ID do canal</label>
        <input id="who" value={who} maxLength={200} onChange={(e) => setWho(e.target.value)} />
        <div className="row">
          <button disabled={!who.trim()} onClick={() => void exportContact()}>
            Baixar dados
          </button>
        </div>
        <label className="check" htmlFor="erase-ok">
          <input id="erase-ok" type="checkbox" checked={confirmErase} onChange={(e) => setConfirmErase(e.target.checked)} />
          Confirmo que quero apagar este contato e tudo o que depende dele (conversas, mensagens e casos). Não dá para
          desfazer.
        </label>
        <label className="check" htmlFor="erase-block">
          <input id="erase-block" type="checkbox" checked={alsoBlock} onChange={(e) => setAlsoBlock(e.target.checked)} />
          Também não contatar mais (guarda só o telefone ou ID, sem nome nem mensagens).
        </label>
        <div className="row">
          <button className="danger" disabled={!who.trim() || !confirmErase} onClick={() => void eraseContact()}>
            Apagar dados
          </button>
        </div>
        {contactNote && (
          <div className={`alert ${contactNote.kind}`} role="status">
            {contactNote.text}
          </div>
        )}
        <p className="muted">
          Quem já pediu para não ser contatado continua bloqueado depois de apagar. Eventos brutos de checkout
          saem pelo prazo de retenção acima.
        </p>
      </section>

      <section className="card" aria-labelledby="consent-title" style={{ marginTop: 16 }}>
        <h2 id="consent-title" style={{ marginTop: 0 }}>
          Registro de consentimento
        </h2>
        {data.consents.length === 0 ? (
          <p className="muted">Nenhum registro ainda. Ele aparece quando você declara o consentimento em Recuperação.</p>
        ) : (
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Quando</th>
                  <th>Quem</th>
                  <th>O quê</th>
                  <th>Origem</th>
                  <th>Qtde</th>
                </tr>
              </thead>
              <tbody>
                {data.consents.map((c, i) => (
                  <tr key={i}>
                    <td>{when(c.at)}</td>
                    <td>{c.by ?? "—"}</td>
                    <td>{ACTIONS[c.action] ?? c.action}</td>
                    <td>{ORIGINS[c.origin] ?? c.origin}</td>
                    <td>{c.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card" aria-labelledby="terms-title" style={{ marginTop: 16 }}>
        <h2 id="terms-title" style={{ marginTop: 0 }}>
          Termos de uso e política de privacidade
        </h2>
        <div className="alert">{data.terms}</div>
      </section>

      {isOwner && (
        <section className="card" aria-labelledby="del-title" style={{ marginTop: 16 }}>
          <h2 id="del-title" style={{ marginTop: 0 }}>
            Excluir a conta
          </h2>
          {data.deletion.requested_at ? (
            <>
              <div className="alert bad" role="status">
                Exclusão marcada para {when(data.deletion.due_at ?? data.deletion.requested_at)}. Até lá o serviço fica
                pausado e você ainda pode cancelar.
              </div>
              <div className="row">
                <button onClick={() => void cancelDeletion()}>Cancelar a exclusão</button>
              </div>
            </>
          ) : (
            <>
              <p className="muted">
                Apaga todos os dados do cliente: conversas, contatos, conexões e usuários. Há {data.deletion.grace_days} dias
                de carência para desistir; nesse período o envio e o vendedor IA ficam pausados.
              </p>
              <label htmlFor="del-confirm">Digite EXCLUIR para confirmar</label>
              <input id="del-confirm" value={typed} maxLength={20} onChange={(e) => setTyped(e.target.value)} />
              <div className="row">
                <button className="danger" disabled={typed !== "EXCLUIR"} onClick={() => void requestDeletion()}>
                  Pedir a exclusão da conta
                </button>
              </div>
            </>
          )}
          {deletionNote && (
            <div className="alert bad" role="status">
              {deletionNote}
            </div>
          )}
        </section>
      )}
    </>
  );
}

export default function Page() {
  return (
    <Shell>
      <Page_ />
    </Shell>
  );
}
