"use client";

import { useState } from "react";
import { API_URL, api, getStoredTenant } from "@/lib/api";

type Item = {
  name: string;
  category: string;
  description: string;
  price_cents: number | null;
  needs_review: boolean;
};
type Row = Item & { selected: boolean; priceText: string };
type Preview = { items: Item[]; warnings: string[] };
type Result = { created: number; duplicates: number };
const MAX_FILES = 80;

function priceInput(value: number | null): string {
  return value === null ? "" : (value / 100).toFixed(2).replace(".", ",");
}

function parsePrice(value: string): number | null {
  const text = value.trim().replace(/^R\$\s*/, "");
  let normalized: string;
  if (/^\d{1,6}(\.\d{3})*,\d{1,2}$/.test(text)) {
    normalized = text.replace(/\./g, "").replace(",", ".");
  } else if (/^\d{1,6}([.,]\d{1,2})?$/.test(text)) {
    normalized = text.replace(",", ".");
  } else {
    return null;
  }
  const number = Number(normalized);
  return Number.isFinite(number) && number >= 0 && number <= 1_000_000
    ? Math.round(number * 100)
    : null;
}

async function sendMenuFile(file: File): Promise<Preview> {
  let mime = file.type.toLowerCase();
  const extension = file.name.split(".").pop()?.toLowerCase();
  if (extension === "csv" && (!mime || mime === "application/vnd.ms-excel")) mime = "text/csv";
  if (extension === "pdf" && !mime) mime = "application/pdf";
  if (!["image/jpeg", "image/png", "image/webp", "application/pdf", "text/csv"].includes(mime)) {
    throw new Error("Use JPG, PNG, WebP, PDF ou CSV.");
  }
  if (file.size > 8_000_000) throw new Error("O arquivo pode ter até 8 MB.");
  const headers = new Headers({ "Content-Type": mime });
  const tenant = getStoredTenant();
  if (tenant) headers.set("X-Tenant-Id", tenant);
  const response = await fetch(API_URL + "/v1/seller/menu-import/preview", {
    method: "POST", headers, credentials: "include", body: file,
  });
  const result = await response.json().catch(() => ({})) as Preview & {
    error?: { message?: string };
  };
  if (!response.ok) throw new Error(result.error?.message ?? "Falha ao ler o cardápio.");
  return result;
}

export function MenuImporter({ onChanged }: { onChanged: () => void }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [defaultLink, setDefaultLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const selected = rows.filter((r) => r.selected);

  function patch(i: number, data: Partial<Row>) {
    setRows((before) => before.map((row, index) => index === i ? { ...row, ...data } : row));
  }

  async function read(file: File | undefined) {
    if (!file || busy) return;
    setBusy(true);
    setFeedback(null);
    try {
      const result = await sendMenuFile(file);
      const proposed: Row[] = result.items.map((row) => ({
        ...row, selected: true, priceText: priceInput(row.price_cents),
      }));
      setRows((current) => {
        const keys = new Set(current.map((r) => (r.name + "|" + r.category).toLowerCase()));
        const fresh = proposed.filter((r) => {
          const key = (r.name + "|" + r.category).toLowerCase();
          if (keys.has(key)) return false;
          keys.add(key);
          return true;
        });
        return [...current, ...fresh].slice(0, MAX_FILES);
      });
      setWarnings(result.warnings);
      setFeedback("Cardápio analisado. Confira cada produto e preço antes de publicar.");
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : "Não foi possível ler o arquivo.");
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    if (busy || selected.length === 0) return;
    const invalid = selected.some((r) => !r.name.trim() || parsePrice(r.priceText) === null);
    if (invalid) {
      setFeedback("Preencha os nomes e confirme os preços de todos os itens selecionados.");
      return;
    }
    try {
      const u = new URL(defaultLink);
      if (u.protocol !== "https:" || !u.hostname) throw new Error("invalid");
    } catch {
      setFeedback("Informe um link HTTPS válido para os pedidos ou pagamento.");
      return;
    }
    setBusy(true);
    setFeedback(null);
    try {
      const result = await api<Result>("/v1/seller/menu-import/commit", {
        method: "POST",
        body: JSON.stringify({
          payment_url: defaultLink.trim(),
          items: selected.map((r) => ({
            name: r.name.trim(),
            category: r.category.trim(),
            description: r.description.trim(),
            price_cents: parsePrice(r.priceText),
          })),
        }),
      });
      setRows([]);
      setWarnings([]);
      setFeedback(
        result.created + " produtos publicados." +
        (result.duplicates ? " " + result.duplicates + " já existiam e foram ignorados." : ""),
      );
      onChanged();
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : "Falha ao publicar o cardápio.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-label="Importar cardápio por foto ou arquivo">
      <strong>Importar cardápio por foto ou arquivo</strong>
      <p className="muted">
        Fotografe o cardápio ou envie JPG, PNG, WebP, PDF (até 12 páginas) ou CSV.
        A IA apenas sugere produtos: confira os preços antes de confirmar.
      </p>
      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <label>
          <span>Selecionar cardápio</span>
          <input type="file" accept=".jpg,.jpeg,.png,.webp,.pdf,.csv,image/jpeg,image/png,image/webp,application/pdf,text/csv"
            disabled={busy} onChange={(e) => { void read(e.currentTarget.files?.[0]); e.currentTarget.value = ""; }} />
        </label>
        <label>
          <span>Tirar foto com a câmera</span>
          <input type="file" accept="image/jpeg,image/png,image/webp" capture="environment"
            disabled={busy} onChange={(e) => { void read(e.currentTarget.files?.[0]); e.currentTarget.value = ""; }} />
        </label>
      </div>
      {busy && <p role="status">Processando cardápio...</p>}
      {feedback && <p role="status">{feedback}</p>}
      {warnings.map((warning, index) => <p className="muted" key={index}>{warning}</p>)}
      {rows.length > 0 && (
        <>
          <h3>Revisão antes da publicação ({selected.length} selecionados)</h3>
          <p className="muted">Selecione os produtos desejados. Edite nomes, categorias, descrições e preços. Nenhuma informação é publicada sem sua confirmação.</p>
          <div style={{ display: "grid", gap: 12 }}>
            {rows.map((row, index) => (
              <div className="card" key={index} style={{ padding: 12, margin: 0 }}>
                <label className="check">
                  <input type="checkbox" checked={row.selected} disabled={busy}
                    onChange={(e) => patch(index, { selected: e.target.checked })} />
                  <strong>Incluir produto {index + 1}</strong>
                </label>
                <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
                  <label>Nome
                    <input value={row.name} maxLength={120} disabled={busy}
                      onChange={(e) => patch(index, { name: e.target.value })} />
                  </label>
                  <label>Categoria
                    <input value={row.category} maxLength={80} disabled={busy}
                      onChange={(e) => patch(index, { category: e.target.value })} />
                  </label>
                  <label>Preço em R$
                    <input inputMode="decimal" value={row.priceText}
                      placeholder="49,90" disabled={busy} aria-invalid={row.selected && parsePrice(row.priceText) === null}
                      onChange={(e) => patch(index, { priceText: e.target.value })} />
                  </label>
                </div>
                <label>Descrição e ingredientes
                  <textarea value={row.description} maxLength={1000} disabled={busy}
                    onChange={(e) => patch(index, { description: e.target.value })} />
                </label>
                {row.needs_review && <p className="muted">Preço não identificado com segurança. Confirme manualmente.</p>}
              </div>
            ))}
          </div>
          <label>Link HTTPS de pedidos ou pagamento (aplicado aos produtos importados)
            <input type="url" placeholder="https://..." value={defaultLink} maxLength={500}
              disabled={busy} onChange={(e) => setDefaultLink(e.target.value)} />
          </label>
          <p className="muted">A importação cadastra os produtos no vendedor IA. Fotos individuais de cada produto podem ser adicionadas depois na galeria.</p>
          <div className="row">
            <button className="primary" disabled={busy || selected.length === 0}
              onClick={() => void publish()}>Conferi os dados — publicar {selected.length} produtos</button>
            <button disabled={busy} onClick={() => { setRows([]); setWarnings([]); setFeedback(null); }}>
              Descartar prévia
            </button>
          </div>
        </>
      )}
    </section>
  );
}
