"""Normaliza eventos de checkout (Cakto, Hotmart) para um formato único.

Estado de confirmação (ver docs/LANCAMENTO_MVP.md):
- Cakto: nomes de evento e campos seguem a documentação oficial (docs.cakto.com.br, guia de
  Webhooks, conferida em 04/10/2026). Ainda NÃO conferido com evento real da conta.
- Hotmart: a documentação oficial não pôde ser lida (acesso recusado). Nomes de evento e
  caminhos de campo são de fonte de terceiros e tentativas tolerantes: NÃO CONFIRMADO.
  Antes de produção, capture um evento real e ajuste os caminhos da Hotmart abaixo.
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


# Pedido (purchase_*, pix_gerado, boleto_gerado, subscription_*, refund, chargeback):
# data.{id, customer{name,email,phone}, product{id,name}, offer{id,price}, amount, checkoutUrl}.
# Carrinho abandonado tem outro formato (sem id, status ou amount): customerName/customerEmail/
# customerCellphone, offer.price, checkoutUrl, createdAt.
_CAKTO_PATHS: dict[str, tuple[str, ...]] = {
    "email": ("data.customer.email", "data.customerEmail"),
    "phone": ("data.customer.phone", "data.customerCellphone"),
    "name": ("data.customer.name", "data.customerName"),
    "product_id": ("data.product.id",),
    "product_name": ("data.product.name",),
    "ref": ("data.id",),
    "amount": ("data.amount", "data.offer.price"),
    "url": ("data.checkoutUrl",),
}
# No boleto, o link útil é o do próprio boleto; se faltar, vale o do checkout.
_CAKTO_BOLETO_URL: tuple[str, ...] = ("data.boleto.boletoUrl", "data.checkoutUrl")

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


def cakto_order(payload: dict[str, Any]) -> dict[str, Any]:
    """Devolve o payload com `data` como objeto.

    No Webhook V2 da Cakto `data` é uma lista com todos os pedidos da mesma cobrança
    (ex.: produto principal + order bump). Usamos o pedido principal (`offer_type == "main"`),
    ou o primeiro se nenhum for marcado.
    """
    data = payload.get("data")
    if not isinstance(data, list):
        return payload
    orders = [d for d in data if isinstance(d, dict)]
    chosen = next((d for d in orders if d.get("offer_type") == "main"), None)
    if chosen is None and orders:
        chosen = orders[0]
    return {**payload, "data": chosen or {}}


def cakto_dedupe_ref(payload: dict[str, Any]) -> str:
    """Referência estável do evento (doc oficial): `data.id`; sem ele (carrinho abandonado),
    e-mail + oferta + `createdAt`. Vazio se não for possível montar."""
    p = cakto_order(payload)
    order_id = text(dig(p, "data.id"))
    if order_id:
        return order_id
    email = text(dig(p, "data.customerEmail")).lower()
    offer = text(dig(p, "data.offer.id"))
    created = text(dig(p, "data.createdAt"))
    if email and offer and created:
        return f"{email}|{offer}|{created}"
    return ""


def normalize_cakto(payload: dict[str, Any]) -> CheckoutEvent | None:
    source = text(payload.get("event"))
    kind = CAKTO_EVENTS.get(source)
    if kind is None:
        return None
    paths = _CAKTO_PATHS
    if kind == BOLETO_PENDING:
        paths = {**paths, "url": _CAKTO_BOLETO_URL}
    return _build(kind, source, cakto_order(payload), paths)


def normalize_hotmart(payload: dict[str, Any]) -> CheckoutEvent | None:
    source = text(payload.get("event"))
    kind = HOTMART_EVENTS.get(source)
    return None if kind is None else _build(kind, source, payload, _HOTMART_PATHS)


NORMALIZERS = {"cakto": normalize_cakto, "hotmart": normalize_hotmart}

# Para a conferência com eventos reais (events/compare.py): os mesmos dados que o normalizador usa.
EVENTS_BY_PROVIDER: dict[str, dict[str, str]] = {"cakto": CAKTO_EVENTS, "hotmart": HOTMART_EVENTS}
PATHS_BY_PROVIDER: dict[str, dict[str, tuple[str, ...]]] = {
    "cakto": _CAKTO_PATHS,
    "hotmart": _HOTMART_PATHS,
}
