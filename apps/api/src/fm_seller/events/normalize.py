"""Normaliza eventos de checkout (Cakto, Hotmart) para um formato único.

ATENÇÃO — formato a confirmar: os NOMES de evento da Cakto vêm da documentação oficial;
os da Hotmart vêm de fonte de terceiros. Os CAMINHOS dos campos dentro do payload (cliente,
produto, valor) são tentativas tolerantes (vários caminhos candidatos) e ainda não foram
conferidos com um evento real.
Antes de ir para produção, capture um evento real de cada plataforma e ajuste as listas abaixo.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

# Tipos de evento que o produto entende.
ABANDONED_CART = "abandoned_cart"
PIX_PENDING = "pix_pending"
BOLETO_PENDING = "boleto_pending"
PURCHASE_REFUSED = "purchase_refused"
PURCHASE_APPROVED = "purchase_approved"
REFUNDED = "refunded"
SUBSCRIPTION_ACTIVE = "subscription_active"
SUBSCRIPTION_LATE = "subscription_late"
SUBSCRIPTION_RECOVERED = "subscription_recovered"
SUBSCRIPTION_CANCELED = "subscription_canceled"

# Oportunidades que nascem dentro do próprio produto (não vêm de checkout).
QUOTE_PENDING = "quote_pending"  # orçamento/pedido em aberto (manual ou planilha)
CONVERSATION_COLD = "conversation_cold"  # conversa que esfriou

CHECKOUT_TRIGGERS = (ABANDONED_CART, PIX_PENDING, BOLETO_PENDING, PURCHASE_REFUSED)
RECOVERY_TRIGGERS = (*CHECKOUT_TRIGGERS, QUOTE_PENDING, CONVERSATION_COLD)

CAKTO_EVENTS: dict[str, str] = {
    "checkout_abandonment": ABANDONED_CART,
    "pix_gerado": PIX_PENDING,
    "boleto_gerado": BOLETO_PENDING,
    "purchase_refused": PURCHASE_REFUSED,
    "purchase_approved": PURCHASE_APPROVED,
    "refund": REFUNDED,
    "chargeback": REFUNDED,
    "subscription_created": SUBSCRIPTION_ACTIVE,
    "subscription_renewed": SUBSCRIPTION_ACTIVE,
    "subscription_resumed": SUBSCRIPTION_ACTIVE,
    "subscription_late": SUBSCRIPTION_LATE,
    "subscription_renewal_refused": SUBSCRIPTION_LATE,
    "subscription_late_recovered": SUBSCRIPTION_RECOVERED,
    "subscription_canceled": SUBSCRIPTION_CANCELED,
}

HOTMART_EVENTS: dict[str, str] = {
    "PURCHASE_OUT_OF_SHOPPING_CART": ABANDONED_CART,
    "PURCHASE_BILLET_PRINTED": BOLETO_PENDING,
    "PURCHASE_APPROVED": PURCHASE_APPROVED,
    "PURCHASE_COMPLETE": PURCHASE_APPROVED,
    "PURCHASE_REFUNDED": REFUNDED,
    "PURCHASE_CHARGEBACK": REFUNDED,
    "PURCHASE_DELAYED": SUBSCRIPTION_LATE,
    "PURCHASE_CANCELED": SUBSCRIPTION_CANCELED,
    "SUBSCRIPTION_CANCELLATION": SUBSCRIPTION_CANCELED,
}


@dataclass(frozen=True)
class CheckoutEvent:
    kind: str
    source_event: str
    external_ref: str
    name: str
    email: str | None
    phone: str | None  # só dígitos com DDI (ex.: 5511999998888), ou None se inválido
    product_id: str
    product_name: str
    amount_cents: int
    payment_url: str | None


def dig(payload: Any, path: str) -> Any:
    cur = payload
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def first(payload: Any, paths: tuple[str, ...]) -> Any:
    for path in paths:
        value = dig(payload, path)
        if value not in (None, "", [], {}):
            return value
    return None


def text(value: Any) -> str:
    if value is None or isinstance(value, dict | list):
        return ""
    return str(value).strip()


def normalize_phone_br(raw: Any) -> str | None:
    """Telefone brasileiro em dígitos com DDI 55. Devolve None se não parecer válido."""
    digits = re.sub(r"\D", "", text(raw))
    if not digits:
        return None
    digits = digits.removeprefix("00")
    if len(digits) in (10, 11):
        digits = "55" + digits
    if digits.startswith("55") and len(digits) in (12, 13):
        return digits
    return None


def to_cents(raw: Any) -> int:
    """Valor em reais (decimal) para centavos. Ver aviso do topo: unidade a confirmar."""
    try:
        value = Decimal(str(raw).replace(",", "."))
    except (InvalidOperation, ValueError):
        return 0
    if value < 0:
        return 0
    return int((value * 100).to_integral_value())


def _fallback_ref(kind: str, email: str | None, phone: str | None, product_id: str) -> str:
    key = f"{kind}|{(email or '').lower()}|{phone or ''}|{product_id}"
    return "auto-" + hashlib.sha256(key.encode()).hexdigest()[:24]


def _build(
    kind: str, source: str, p: dict[str, Any], paths: dict[str, tuple[str, ...]]
) -> CheckoutEvent:
    email = text(first(p, paths["email"])).lower() or None
    phone = normalize_phone_br(first(p, paths["phone"]))
    product_id = text(first(p, paths["product_id"]))
    ref = text(first(p, paths["ref"])) or _fallback_ref(kind, email, phone, product_id)
    url = text(first(p, paths["url"])) or None
    return CheckoutEvent(
        kind=kind,
        source_event=source,
        external_ref=ref,
        name=text(first(p, paths["name"])),
        email=email,
        phone=phone,
        product_id=product_id,
        product_name=text(first(p, paths["product_name"])),
        amount_cents=to_cents(first(p, paths["amount"])),
        payment_url=url if url and url.startswith(("http://", "https://")) else None,
    )


_CAKTO_PATHS: dict[str, tuple[str, ...]] = {
    "email": ("data.customer.email", "data.buyer.email", "customer.email"),
    "phone": ("data.customer.phone", "data.customer.cellphone", "data.customer.whatsapp"),
    "name": ("data.customer.name", "data.buyer.name", "customer.name"),
    "product_id": ("data.product.id", "data.offer.id", "data.product_id"),
    "product_name": ("data.product.name", "data.offer.name", "data.productName"),
    "ref": ("data.id", "data.refId", "data.order.id", "id"),
    "amount": ("data.amount", "data.baseAmount", "data.offer.price", "data.product.price"),
    "url": ("data.checkoutUrl", "data.checkout_url", "data.boleto.url", "data.boletoUrl"),
}

_HOTMART_PATHS: dict[str, tuple[str, ...]] = {
    "email": ("data.buyer.email", "data.buyer.checkout_email"),
    "phone": ("data.buyer.checkout_phone", "data.buyer.phone", "data.buyer.phone_number"),
    "name": ("data.buyer.name",),
    "product_id": ("data.product.id", "data.product.ucode"),
    "product_name": ("data.product.name",),
    "ref": ("data.purchase.transaction", "data.subscription.subscriber.code"),
    "amount": (
        "data.purchase.price.value",
        "data.purchase.full_price.value",
        "data.purchase.original_offer_price.value",
    ),
    "url": ("data.purchase.billet_url", "data.billet.url", "data.purchase.checkout_url"),
}


def normalize_cakto(payload: dict[str, Any]) -> CheckoutEvent | None:
    source = text(payload.get("event"))
    kind = CAKTO_EVENTS.get(source)
    return None if kind is None else _build(kind, source, payload, _CAKTO_PATHS)


def normalize_hotmart(payload: dict[str, Any]) -> CheckoutEvent | None:
    source = text(payload.get("event"))
    kind = HOTMART_EVENTS.get(source)
    return None if kind is None else _build(kind, source, payload, _HOTMART_PATHS)


NORMALIZERS = {"cakto": normalize_cakto, "hotmart": normalize_hotmart}
