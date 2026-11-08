from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from fm_seller.ai.model import build_ai_model
from fm_seller.api.deps import get_db, get_principal
from fm_seller.errors import bad_request
from fm_seller.seller.menu_import import (
    MAX_MENU_BYTES,
    MIME_TYPES,
    reserve_ai_import,
)
from fm_seller.seller.menu_import import (
    preview as preview_menu_document,
)
from fm_seller.seller.photos import MAX_UPLOAD
from fm_seller.seller.service import SellerService
from fm_seller.services import Principal

router = APIRouter(tags=["vendedor"])
Who = Annotated[Principal, Depends(get_principal)]


def svc(request: Request) -> SellerService:
    available = build_ai_model(request.app.state.settings).available
    return SellerService(get_db(request), available)


Svc = Annotated[SellerService, Depends(svc)]


class OfferIn(BaseModel):
    name: str = Field(max_length=120)
    description: str = Field(default="", max_length=1000)
    brand: str = Field(default="", max_length=120)
    category: str = Field(default="", max_length=80)
    price_cents: int = Field(ge=0)
    payment_url: str = Field(max_length=500)
    active: bool = True


class KnowledgeIn(BaseModel):
    question: str = Field(max_length=200)
    answer: str = Field(max_length=700)
    active: bool = True


class SettingsIn(BaseModel):
    ai_enabled: bool
    ai_persona: str = Field(default="", max_length=600)


class TurnIn(BaseModel):
    role: Literal["customer", "assistant"]
    text: str = Field(max_length=500)


class SandboxIn(BaseModel):
    messages: list[TurnIn] = Field(min_length=1, max_length=12)


class ReplyIn(BaseModel):
    body: str = Field(max_length=1000)


class StatusIn(BaseModel):
    status: str = Field(max_length=10)


@router.get("/seller/offers")
def offers(s: Svc, p: Who) -> list[dict[str, Any]]:
    return s.offers(p)


@router.post("/seller/offers")
def create_offer(body: OfferIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.create_offer(
        p,
        body.name,
        body.description,
        body.price_cents,
        body.payment_url,
        body.brand,
        body.category,
    )


@router.put("/seller/offers/{offer_id}")
def update_offer(offer_id: uuid.UUID, body: OfferIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.update_offer(
        p,
        offer_id,
        body.name,
        body.description,
        body.price_cents,
        body.payment_url,
        body.active,
        body.brand,
        body.category,
    )


@router.delete("/seller/offers/{offer_id}")
def delete_offer(offer_id: uuid.UUID, s: Svc, p: Who) -> dict[str, str]:
    s.delete_offer(p, offer_id)
    return {"status": "removed"}


@router.post("/seller/offers/{offer_id}/photos")
async def upload_offer_photo(
    offer_id: uuid.UUID, request: Request, s: Svc, p: Who
) -> dict[str, Any]:
    # No multipart parser, external URLs or public media buckets.
    # Stream with an explicit cap to avoid unbounded request buffering.
    if request.headers.get("content-type", "").split(";")[0] not in (
        "image/jpeg",
        "image/png",
        "image/webp",
    ):
        raise bad_request("invalid_image_type", "Envie uma imagem JPG, PNG ou WebP.")
    supplied_length = request.headers.get("content-length")
    if supplied_length is not None and (
        not supplied_length.isdecimal() or int(supplied_length) > MAX_UPLOAD
    ):
        raise bad_request("invalid_image_size", "Foto acima de 3 MB.")
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_UPLOAD:
            raise bad_request("invalid_image_size", "Foto acima de 3 MB.")
        payload.extend(chunk)
    return s.upload_offer_photo(p, offer_id, bytes(payload))


@router.get("/seller/offers/{offer_id}/photos/{photo_id}")
def get_offer_photo(offer_id: uuid.UUID, photo_id: uuid.UUID, s: Svc, p: Who) -> Response:
    raw = s.get_offer_photo(p, offer_id, photo_id)
    return Response(
        content=raw,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/seller/offers/{offer_id}/photos/{photo_id}")
def delete_offer_photo(offer_id: uuid.UUID, photo_id: uuid.UUID, s: Svc, p: Who) -> dict[str, str]:
    s.delete_offer_photo(p, offer_id, photo_id)
    return {"status": "removed"}


class MenuItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=1000)
    price_cents: int = Field(ge=0, le=100_000_000)


class MenuCommitIn(BaseModel):
    items: list[MenuItemIn] = Field(min_length=1, max_length=80)
    payment_url: str = Field(min_length=8, max_length=500)


@router.post("/seller/menu-import/preview")
async def preview_menu(request: Request, s: Svc, p: Who) -> dict[str, Any]:
    # Deny unauthorized clients before reading bytes or incurring an AI charge.
    s._guard(p, edit=True)
    mime = request.headers.get("content-type", "").split(";")[0].lower().strip()
    if mime not in MIME_TYPES:
        raise bad_request("menu_format", "Use foto JPG/PNG/WebP, PDF ou CSV.")
    length = request.headers.get("content-length")
    if length is not None and (not length.isdecimal() or int(length) > MAX_MENU_BYTES):
        raise bad_request("menu_size", "Arquivo acima de 8 MB.")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_MENU_BYTES:
            raise bad_request("menu_size", "Arquivo acima de 8 MB.")
        data.extend(chunk)
    if not data:
        raise bad_request("menu_size", "Selecione um arquivo válido.")
    if mime != "text/csv":
        with s._tx(p) as conn:
            reserve_ai_import(conn, p)
    return await run_in_threadpool(
        preview_menu_document, bytes(data), mime, request.app.state.settings
    )


@router.post("/seller/menu-import/commit")
def publish_menu(body: MenuCommitIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.import_menu(p, [item.model_dump() for item in body.items], body.payment_url)


@router.get("/seller/knowledge")
def knowledge(s: Svc, p: Who) -> list[dict[str, Any]]:
    return s.knowledge(p)


@router.post("/seller/knowledge")
def create_knowledge(body: KnowledgeIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.create_knowledge(p, body.question, body.answer)


@router.put("/seller/knowledge/{item_id}")
def update_knowledge(item_id: uuid.UUID, body: KnowledgeIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.update_knowledge(p, item_id, body.question, body.answer, body.active)


@router.delete("/seller/knowledge/{item_id}")
def delete_knowledge(item_id: uuid.UUID, s: Svc, p: Who) -> dict[str, str]:
    s.delete_knowledge(p, item_id)
    return {"status": "removed"}


@router.get("/seller/settings")
def get_settings(s: Svc, p: Who) -> dict[str, Any]:
    return s.get_settings(p)


@router.get("/seller/usage")
def usage(s: Svc, p: Who) -> dict[str, Any]:
    return s.usage(p)


@router.put("/seller/settings")
def put_settings(body: SettingsIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.put_settings(p, body.ai_enabled, body.ai_persona)


@router.post("/seller/sandbox")
def sandbox(body: SandboxIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.sandbox(p, [(m.role, m.text) for m in body.messages])


@router.get("/seller/handoffs")
def handoffs(s: Svc, p: Who, days: Annotated[int, Query(ge=1, le=365)] = 30) -> dict[str, Any]:
    return s.handoffs(p, days)


@router.get("/inbox")
def inbox(
    s: Svc, p: Who, status: str | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[dict[str, Any]]:
    return s.inbox(p, status, limit)


@router.get("/inbox/{conv_id}")
def messages(conv_id: uuid.UUID, s: Svc, p: Who) -> dict[str, Any]:
    return s.messages(p, conv_id)


@router.post("/inbox/{conv_id}/reply")
def reply(conv_id: uuid.UUID, body: ReplyIn, s: Svc, p: Who) -> dict[str, str]:
    return s.reply(p, conv_id, body.body)


@router.post("/inbox/{conv_id}/status")
def set_status(conv_id: uuid.UUID, body: StatusIn, s: Svc, p: Who) -> dict[str, str]:
    return s.set_status(p, conv_id, body.status)
