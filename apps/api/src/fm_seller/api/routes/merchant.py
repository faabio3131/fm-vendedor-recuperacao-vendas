"""Merchant-owned sales: hosted checkout links and verified payment notifications."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from fm_seller.api.deps import get_db, get_principal
from fm_seller.merchant.service import MerchantService
from fm_seller.merchant.webhook_multi import receive_gateway_event
from fm_seller.services import Principal

router = APIRouter(prefix="/merchant", tags=["pedidos e pagamentos"])
webhook_router = APIRouter(prefix="/webhooks/merchant", tags=["webhook financeiro"])
Who = Annotated[Principal, Depends(get_principal)]


def service(request: Request) -> MerchantService:
    return MerchantService(
        get_db(request),
        request.app.state.box,
        request.app.state.settings,
        provider=request.app.state.merchant_provider,
    )


Svc = Annotated[MerchantService, Depends(service)]


class AccountIn(BaseModel):
    provider: Literal["asaas", "mercadopago", "pagbank", "pagarme", "picpay"] = "asaas"
    mode: Literal["sandbox", "production"]
    access_token: str = Field(min_length=20, max_length=512)
    webhook_token: str = Field(min_length=32, max_length=255)
    delivery_fee_cents: int = Field(default=0, ge=0, le=100_000)


class OrderItemIn(BaseModel):
    offer_id: uuid.UUID
    quantity: int = Field(ge=1, le=99)


class AddressIn(BaseModel):
    postal_code: str = Field(default="", max_length=9)
    street: str = Field(default="", max_length=150)
    street_number: str = Field(default="", max_length=25)
    neighborhood: str = Field(default="", max_length=100)
    city: str = Field(default="", max_length=100)
    state: str = Field(default="", max_length=2)
    complement: str = Field(default="", max_length=120)
    reference: str = Field(default="", max_length=120)


class OrderIn(BaseModel):
    idempotency_key: str = Field(min_length=12, max_length=100)
    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str = Field(default="", max_length=25)
    customer_email: str = Field(default="", max_length=254)
    items: list[OrderItemIn] = Field(min_length=1, max_length=20)
    method: Literal["pickup", "delivery"] = "pickup"
    address: AddressIn = Field(default_factory=AddressIn)


class DeliveryZoneIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    start: str = Field(min_length=8, max_length=8)
    end: str = Field(min_length=8, max_length=8)
    fee_cents: int = Field(ge=0, le=100_000)


class ChargeIn(BaseModel):
    customer_cpf_cnpj: str = Field(min_length=11, max_length=25)


@router.get("/account")
def get_account(s: Svc, p: Who) -> dict[str, Any]:
    return s.get_account(p)


@router.put("/account")
def put_account(body: AccountIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.configure(
        p,
        body.mode,
        body.access_token,
        body.webhook_token,
        body.delivery_fee_cents,
        body.provider,
    )


@router.get("/delivery/zones")
def delivery_zones(s: Svc, p: Who) -> list[dict[str, Any]]:
    return s.delivery_zones(p)


@router.post("/delivery/zones")
def add_zone(body: DeliveryZoneIn, s: Svc, p: Who) -> dict[str, str]:
    return s.save_delivery_zone(p, body.name, body.start, body.end, body.fee_cents)


@router.put("/delivery/zones/{zone_id}")
def edit_zone(zone_id: uuid.UUID, body: DeliveryZoneIn, s: Svc, p: Who) -> dict[str, str]:
    return s.save_delivery_zone(p, body.name, body.start, body.end, body.fee_cents, zone_id)


@router.delete("/delivery/zones/{zone_id}")
def disable_zone(zone_id: uuid.UUID, s: Svc, p: Who) -> dict[str, str]:
    return s.disable_delivery_zone(p, zone_id)


@router.get("/orders")
def orders(s: Svc, p: Who) -> list[dict[str, Any]]:
    return s.orders(p)


@router.get("/orders/{order_id}")
def get_order(order_id: uuid.UUID, s: Svc, p: Who) -> dict[str, Any]:
    return s.order(p, order_id)


@router.post("/orders")
def create_order(body: OrderIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.create_order(
        p,
        body.idempotency_key,
        body.customer_name,
        body.customer_phone,
        body.customer_email,
        [(x.offer_id, x.quantity) for x in body.items],
        method=body.method,
        address=body.address.model_dump(),
    )


@router.post("/orders/{order_id}/charge")
def generate_charge(
    order_id: uuid.UUID,
    body: ChargeIn,
    s: Svc,
    p: Who,
) -> dict[str, Any]:
    return s.charge(p, order_id, body.customer_cpf_cnpj)


@router.post("/orders/{order_id}/reconcile")
def reconcile(order_id: uuid.UUID, s: Svc, p: Who) -> dict[str, Any]:
    return s.reconcile(p, order_id)


@webhook_router.post("/asaas/{public_id}")
async def receive_asaas(public_id: str, request: Request) -> dict[str, str]:
    raw = await request.body()
    s = service(request)
    result = s.receive_event(
        public_id,
        request.headers.get("asaas-access-token", ""),
        raw,
    )
    return {"status": result}


@webhook_router.post("/{provider}/{public_id}")
async def receive_merchant_gateway(
    provider: str,
    public_id: str,
    request: Request,
) -> dict[str, str]:
    raw = await request.body()
    result = receive_gateway_event(
        service(request),
        provider,
        public_id,
        request.headers,
        request.query_params,
        raw,
    )
    return {"status": result}
