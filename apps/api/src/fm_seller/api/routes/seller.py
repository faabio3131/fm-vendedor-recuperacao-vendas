from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from fm_seller.ai.model import build_ai_model
from fm_seller.api.deps import get_db, get_principal
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
    price_cents: int = Field(ge=0)
    payment_url: str = Field(max_length=500)
    active: bool = True


class SettingsIn(BaseModel):
    ai_enabled: bool
    ai_persona: str = Field(default="", max_length=600)


class ReplyIn(BaseModel):
    body: str = Field(max_length=1000)


class StatusIn(BaseModel):
    status: str = Field(max_length=10)


@router.get("/seller/offers")
def offers(s: Svc, p: Who) -> list[dict[str, Any]]:
    return s.offers(p)


@router.post("/seller/offers")
def create_offer(body: OfferIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.create_offer(p, body.name, body.description, body.price_cents, body.payment_url)


@router.put("/seller/offers/{offer_id}")
def update_offer(offer_id: uuid.UUID, body: OfferIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.update_offer(
        p, offer_id, body.name, body.description, body.price_cents, body.payment_url, body.active
    )


@router.delete("/seller/offers/{offer_id}")
def delete_offer(offer_id: uuid.UUID, s: Svc, p: Who) -> dict[str, str]:
    s.delete_offer(p, offer_id)
    return {"status": "removed"}


@router.get("/seller/settings")
def get_settings(s: Svc, p: Who) -> dict[str, Any]:
    return s.get_settings(p)


@router.put("/seller/settings")
def put_settings(body: SettingsIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.put_settings(p, body.ai_enabled, body.ai_persona)


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
