"""Private, tenant-bound offer-photo storage and deterministic image selection.

Only verified raster input is accepted. Image bytes are decoded and re-encoded
to a bounded JPEG to strip metadata and active payloads. Never serve public
asset URLs or fetch arbitrary remote images (SSRF resistant).
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from fm_seller.db import Conn
from fm_seller.errors import bad_request, not_found
from fm_seller.money import format_brl
from fm_seller.recovery.optout import normalize

MAX_UPLOAD = 3_000_000
MAX_IMAGE_BYTES = 2_000_000
MAX_PHOTOS_PER_OFFER = 5
MAX_TENANT_BYTES = 100_000_000
MAX_SIDE = 1920
Image.MAX_IMAGE_PIXELS = 12_000_000

PHOTO_WORDS = frozenset(
    {"foto", "fotos", "imagem", "imagens", "fotografia", "fotografias", "picture"}
)
PHOTO_REQUEST_WORDS = frozenset(
    {
        "manda",
        "mande",
        "envia",
        "envie",
        "enviar",
        "quero",
        "ver",
        "mostrar",
        "mostra",
        "mostre",
        "tem",
        "pode",
    }
)
STOP_WORDS = frozenset(
    {
        "foto",
        "fotos",
        "imagem",
        "imagens",
        "manda",
        "mande",
        "me",
        "a",
        "o",
        "as",
        "os",
        "de",
        "do",
        "da",
        "das",
        "dos",
        "por",
        "favor",
        "envie",
        "enviar",
        "envia",
        "uma",
        "um",
        "quero",
        "ver",
        "esse",
        "essa",
        "produto",
        "modelo",
        "pode",
        "mostrar",
        "mostra",
        "mostre",
        "qual",
        "tem",
        "voce",
        "pra",
        "para",
        "e",
        "com",
        "meu",
        "sua",
    }
)


def normalize_image(raw: bytes) -> tuple[bytes, int, int]:
    if not raw or len(raw) > MAX_UPLOAD:
        raise bad_request("invalid_image_size", "A foto deve ter no máximo 3 MB.")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if (
                source.format not in ("JPEG", "PNG", "WEBP")
                or source.width * source.height > 12_000_000
            ):
                raise ValueError("unsupported image")
            source.load()
            fixed = ImageOps.exif_transpose(source)
            if fixed.mode in ("RGBA", "LA") or (fixed.mode == "P" and "transparency" in fixed.info):
                rgb = Image.new("RGB", fixed.size, (255, 255, 255))
                rgba = fixed.convert("RGBA")
                rgb.paste(rgba, mask=rgba.getchannel("A"))
                fixed = rgb
            else:
                fixed = fixed.convert("RGB")
            fixed.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            fixed.save(output, format="JPEG", quality=82, optimize=True, progressive=True)
            content = output.getvalue()
            if len(content) > MAX_IMAGE_BYTES:
                raise bad_request(
                    "image_too_large", "A foto tem muitos detalhes. Use uma imagem menor."
                )
            return content, fixed.width, fixed.height
    except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise bad_request("invalid_image", "Envie uma foto válida em JPG, PNG ou WebP.") from None


def photo_summaries(conn: Conn, tenant_id: uuid.UUID) -> dict[uuid.UUID, list[dict[str, Any]]]:
    rows = conn.execute(
        "SELECT id,offer_id,sort_order,width,height FROM offer_photos WHERE tenant_id=%s "
        "ORDER BY offer_id,sort_order,created_at,id",
        (tenant_id,),
    ).fetchall()
    result: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row["offer_id"], []).append(
            {"id": str(row["id"]), "width": row["width"], "height": row["height"]}
        )
    return result


def create_photo(
    conn: Conn, tenant_id: uuid.UUID, offer_id: uuid.UUID, raw: bytes
) -> dict[str, Any]:
    data, width, height = normalize_image(raw)
    # Lock per tenant: simultaneous uploads cannot skip the photo count/quota.
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"fmav:offer-photos:{tenant_id}",)
    )
    offer = conn.execute(
        "SELECT id FROM offers WHERE tenant_id=%s AND id=%s", (tenant_id, offer_id)
    ).fetchone()
    if offer is None:
        raise not_found("Oferta não encontrada.")
    count = conn.execute(
        "SELECT count(*) AS n,coalesce(max(sort_order),0) AS max_pos "
        "FROM offer_photos WHERE tenant_id=%s AND offer_id=%s",
        (tenant_id, offer_id),
    ).fetchone()
    assert count is not None
    if count["n"] >= MAX_PHOTOS_PER_OFFER:
        raise bad_request("photo_limit", "Limite de cinco fotos por produto.")
    total = conn.execute(
        "SELECT coalesce(sum(octet_length(bytes)),0) AS n FROM offer_photos WHERE tenant_id=%s",
        (tenant_id,),
    ).fetchone()
    assert total is not None
    if total["n"] + len(data) > MAX_TENANT_BYTES:
        raise bad_request("photo_storage_limit", "Limite de armazenamento de fotos atingido.")
    added = conn.execute(
        "INSERT INTO offer_photos(tenant_id,offer_id,sort_order,bytes,width,height) "
        "VALUES(%s,%s,%s,%s,%s,%s) RETURNING id,width,height",
        (tenant_id, offer_id, count["max_pos"] + 1, data, width, height),
    ).fetchone()
    assert added is not None
    return {"id": str(added["id"]), "width": added["width"], "height": added["height"]}


def request_for_photo(question: str) -> bool:
    words = set(normalize(question).split())
    # "Quero ver esse modelo" after the seller has shown a named product is a
    # real photo request even without the literal word "foto".
    viewing = {"ver", "mostrar", "mostra", "mostre", "manda", "mande", "envia", "envie"}
    return bool((words & PHOTO_WORDS and words & PHOTO_REQUEST_WORDS)
                or (words & {"ver", "mostrar", "mostra", "mostre"}))


def photo_reply(
    conn: Conn, tenant_id: uuid.UUID, question: str, offers: Sequence[Mapping[str, Any]],
    *, last_bot_text: str | None = None,
) -> tuple[str, uuid.UUID | None] | None:
    """Never choose an image based on a model's invented SKU or URL."""
    if not request_for_photo(question):
        return None
    with_photos = conn.execute(
        "SELECT op.offer_id,op.id,op.sort_order,o.name,o.description,o.price_cents "
        "FROM offer_photos op JOIN offers o "
        "ON o.id=op.offer_id AND o.tenant_id=op.tenant_id "
        "WHERE op.tenant_id=%s AND o.active ORDER BY op.sort_order,op.created_at "
        "LIMIT 5000",
        (tenant_id,),
    ).fetchall()
    first: dict[uuid.UUID, uuid.UUID] = {}
    catalog: dict[uuid.UUID, dict[str, Any]] = {}
    for row in with_photos:
        first.setdefault(row["offer_id"], row["id"])
        catalog[row["offer_id"]] = {
            "id": row["offer_id"],
            "name": row["name"],
            "description": row["description"],
            "price_cents": row["price_cents"],
        }
    if not first:
        return (
            "As fotos ainda não foram cadastradas no catálogo desta loja. "
            "Posso te informar os detalhes dos produtos disponíveis.",
            None,
        )
    # Includes photos of offers beyond the first 20 entries passed to the language model.
    relevant = list(catalog.values())
    # Require meaningful words from the customer's request (SKU/model/brand).
    tokens = set(normalize(question).split()) - STOP_WORDS
    if tokens:
        scored = []
        for offer in relevant:
            hay = set(normalize(
                str(offer["name"]) + " " + str(offer.get("brand", "")) + " " +
                str(offer["description"])
            ).split())
            match = len(tokens & hay)
            if match:
                scored.append((match, offer))
        if scored:
            best = max(score for score, _ in scored)
            relevant = [o for score, o in scored if score == best]
        else:
            return (
                "Não encontrei esse modelo entre os produtos com fotos cadastradas. "
                "Pode me dizer o nome ou a marca?",
                None,
            )
    if len(relevant) != 1:
        names = ", ".join(str(o["name"]) for o in relevant[:5])
        return (f"Tenho fotos destes produtos: {names}. Qual modelo você quer ver?", None)
    chosen = relevant[0]
    caption = (
        f"Foto de {chosen['name']} — {format_brl(chosen['price_cents'])}. "
        "Quer que eu te passe o link de compra?"
    )
    return (caption, first[chosen["id"]])
