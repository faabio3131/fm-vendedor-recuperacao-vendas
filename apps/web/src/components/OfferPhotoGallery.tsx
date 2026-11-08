"use client";

import { useEffect, useState } from "react";
import { api, apiOfferPhoto, fetchOfferPhoto, type Offer, type OfferPhoto } from "@/lib/api";

function PhotoPreview({ offer, photo }: { offer: Offer; photo: OfferPhoto }) {
  const [source, setSource] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let blobUrl: string | null = null;
    void fetchOfferPhoto(offer.id, photo.id, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        blobUrl = URL.createObjectURL(blob);
        setSource(blobUrl);
      })
      .catch(() => undefined);
    return () => {
      controller.abort();
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [offer.id, photo.id]);

  return (
    <div style={{ height: 106, width: 112, display: "grid", placeItems: "center", border: "1px solid var(--line, #36504c)", borderRadius: 9 }}>
      {source ? (
        <img src={source} alt={`Foto cadastrada de ${offer.name}`} style={{ height: 100, width: 105, objectFit: "contain", borderRadius: 7 }} />
      ) : (
        <span className="muted" style={{ fontSize: 12 }}>Carregando foto</span>
      )}
    </div>
  );
}

export function OfferPhotoGallery({
  offer,
  canEdit,
  onChanged,
}: {
  offer: Offer;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const photos = offer.photos ?? [];
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  async function add(files: FileList | null) {
    if (!files || files.length === 0 || busy) return;
    setFeedback(null);
    setBusy(true);
    try {
      if (photos.length + files.length > 5) throw new Error("Cada produto aceita até cinco fotos.");
      for (const file of Array.from(files)) {
        if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
          throw new Error("Selecione arquivos JPG, PNG ou WebP.");
        }
        if (file.size > 3_000_000) throw new Error("Cada foto pode ter até 3 MB.");
        await apiOfferPhoto(offer.id, file);
      }
      setFeedback("Fotos adicionadas.");
      onChanged();
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : "Falha ao adicionar foto.");
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function remove(photoId: string) {
    if (busy) return;
    setBusy(true);
    setFeedback(null);
    try {
      await api(`/v1/seller/offers/${offer.id}/photos/${photoId}`, { method: "DELETE" });
      setFeedback("Foto removida.");
      onChanged();
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : "Falha ao remover foto.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label={`Fotos de ${offer.name}`} style={{ marginTop: 12 }}>
      <strong>Fotos do produto ({photos.length}/5)</strong>
      <p className="muted">JPG, PNG ou WebP, até 3 MB por arquivo. As fotos ficam privadas e só são enviadas quando o cliente pede para ver um produto.</p>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 10 }}>
        {photos.map((photo) => (
          <div key={photo.id} style={{ display: "grid", justifyItems: "center", gap: 6 }}>
            <PhotoPreview offer={offer} photo={photo} />
            {canEdit && <button disabled={busy} onClick={() => void remove(photo.id)} aria-label={`Remover foto de ${offer.name}`}>Remover</button>}
          </div>
        ))}
      </div>
      {canEdit && photos.length < 5 && (
        <label style={{ display: "inline-block" }}>
          <span className="muted">Adicionar fotos ao produto</span>
          <input
            type="file"
            multiple
            accept="image/jpeg,image/png,image/webp"
            disabled={busy}
            onChange={(e) => { void add(e.currentTarget.files); e.currentTarget.value = ""; }}
          />
        </label>
      )}
      {feedback && <p role="status">{feedback}</p>}
    </section>
  );
}
