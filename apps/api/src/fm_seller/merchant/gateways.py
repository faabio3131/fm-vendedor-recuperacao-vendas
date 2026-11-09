"""Tenant-owned hosted checkout adapters: no card custody or trusting webhook statuses.

All monetary statuses are normalized only from fresh provider GET responses.
Ambiguous external POST outcomes remain locked for reconciliation, never retried blind.
"""
from __future__ import annotations

import base64
import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from fm_seller.merchant.asaas import AsaasClient, ProviderRejected, ProviderUncertain, amount_cents

PROVIDERS = ("asaas", "mercadopago", "pagbank", "pagarme", "picpay")
LABELS = {
    "asaas": "Asaas", "mercadopago": "Mercado Pago",
    "pagbank": "PagBank", "pagarme": "Pagar.me", "picpay": "PicPay",
}
PAY_HOSTS = {
    "mercadopago": {"www.mercadopago.com.br", "mercadopago.com.br",
                    "www.mercadopago.com", "www.mercadopago.com.ar"},
    "pagbank": {"pagamento.pagbank.com.br", "pagamento.pagseguro.uol.com.br",
                "qa.pagamento.pagseguro.uol.com.br", "pagamento.pagseguro.com.br"},
    "pagarme": {"payment-link.pagar.me", "checkout.pagar.me"},
    "picpay": {"link.picpay.com", "link.ppay.me"},
}
HOSTS = {
    "mercadopago": {
        "sandbox": "https://api.mercadopago.com",
        "production": "https://api.mercadopago.com",
    },
    "pagbank": {
        "sandbox": "https://sandbox.api.pagseguro.com",
        "production": "https://api.pagseguro.com",
    },
    "pagarme": {
        "sandbox": "https://sdx-api.pagar.me/core/v5",
        "production": "https://api.pagar.me/core/v5",
    },
    "picpay": {
        "sandbox": "https://api.ms.qa.limbo.work",
        "production": "https://ecommerce-api.svc.picpay.com",
    },
}


def gateway_ref(reference: str, provider: str) -> str:
    """PicPay accepts only 15 chars as order_number."""
    if provider == "picpay":
        return hashlib.sha256(reference.encode()).hexdigest()[:15]
    return reference


def payment_url(provider: str, raw: Any) -> str:
    if not isinstance(raw, str) or not 1 <= len(raw) <= 500:
        raise ProviderUncertain("Missing hosted checkout link")
    try:
        url = httpx.URL(raw)
    except (ValueError, httpx.InvalidURL) as exc:
        raise ProviderUncertain("Invalid checkout URL") from exc
    if (
        url.scheme != "https"
        or url.userinfo
        or url.port not in (None, 443)
        or url.host not in PAY_HOSTS[provider]
    ):
        raise ProviderUncertain("Checkout URL host not approved")
    return raw


def _money(cents: int) -> str:
    return str((Decimal(cents) / 100).quantize(Decimal("0.01")))


def _id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{3,100}", value):
        raise ProviderUncertain("Invalid provider checkout identifier")
    return value


class HostedGateway:
    """Provider-specific host, secrets and request allowlist, with isolated HTTP transport."""

    def __init__(
        self, provider: str, transport: httpx.BaseTransport | None = None,
        *, webhook_url: str | None = None,
    ) -> None:
        if provider not in HOSTS:
            raise ProviderRejected("Unsupported payment provider")
        self.provider = provider
        self.transport = transport
        self.webhook_url = webhook_url

    def _request(
        self, mode: str, key: str, method: str, path: str,
        *, data: dict[str, Any] | None = None, params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if mode not in HOSTS[self.provider] or not key or method not in ("GET", "POST"):
            raise ProviderRejected("Invalid provider configuration")
        if not path.startswith("/") or "//" in path or "?" in path or ".." in path:
            raise ProviderRejected("Invalid provider endpoint")
        allowed = {
            "mercadopago": ("/users/me", "/v1/orders"),
            "pagbank": ("/checkouts", "/public-keys"),
            "pagarme": ("/paymentlinks",),
            "picpay": ("/oauth2/token", "/v1/paymentlink", "/sandbox/v1/paymentlink"),
        }[self.provider]
        if not path.startswith(allowed) or (
            method == "POST" and self.provider == "pagbank" and path != "/checkouts"
        ):
            raise ProviderRejected("Provider operation not permitted")
        if self.provider == "pagarme":
            raw = base64.b64encode((key + ":").encode()).decode()
            headers = {"Authorization": "Basic " + raw}
        elif self.provider == "picpay" and path == "/oauth2/token":
            headers = {}
        else:
            headers = {"Authorization": "Bearer " + key}
        headers.update({"Accept": "application/json", "User-Agent": "AtendeVendeIA/2"})
        if extra_headers:
            headers.update(extra_headers)
        try:
            with httpx.Client(
                timeout=httpx.Timeout(12.0, connect=4.0),
                follow_redirects=False, trust_env=False, transport=self.transport,
            ) as client:
                resp = client.request(method, HOSTS[self.provider][mode] + path,
                                      headers=headers, json=data, params=params)
        except httpx.TransportError as exc:
            raise ProviderUncertain("Provider connection uncertain") from exc
        if resp.status_code >= 500 or resp.status_code in (408, 409, 425, 429):
            raise ProviderUncertain("Provider response uncertain")
        if resp.status_code >= 400:
            raise ProviderRejected(f"Provider declined ({resp.status_code})")
        try:
            answer: Any = resp.json()
        except ValueError as exc:
            raise ProviderUncertain("Provider response unparsable") from exc
        if not isinstance(answer, dict):
            raise ProviderUncertain("Unexpected provider response")
        return answer

    def _token(self, mode: str, key: str) -> str:
        if self.provider != "picpay":
            return key
        try:
            client_id, client_secret = key.split(":", 1)
        except ValueError as exc:
            raise ProviderRejected("PicPay requires client_id:client_secret") from exc
        if len(client_id) < 4 or len(client_secret) < 8:
            raise ProviderRejected("Invalid PicPay OAuth credentials")
        answer = self._request(
            mode, key, "POST", "/oauth2/token",
            data={"grant_type": "client_credentials",
                  "client_id": client_id, "client_secret": client_secret},
        )
        token = answer.get("access_token") or answer.get("accessToken")
        if not isinstance(token, str) or not 10 <= len(token) <= 5000:
            raise ProviderUncertain("PicPay token unavailable")
        return token

    def verify_account(self, mode: str, key: str) -> bool:
        if self.provider == "mercadopago":
            return bool(self._request(mode, key, "GET", "/users/me").get("id"))
        if self.provider == "pagbank":
            # A merchant must provision a webhook public key in PagBank first.
            row = self._request(mode, key, "GET", "/public-keys",
                                params={"type": "webhook"})
            return isinstance(row.get("public_key"), str) and bool(row["public_key"])
        if self.provider == "pagarme":
            row = self._request(mode, key, "GET", "/paymentlinks",
                                params={"page": "1", "per_page": "1"})
            return isinstance(row.get("data"), list)
        return bool(self._token(mode, key))

    def customer(self, mode: str, key: str, name: str, cpf_cnpj: str, reference: str) -> str:
        # Hosted checkouts collect payer data securely. Keep CPF outside database.
        return cpf_cnpj

    def find_charge(self, mode: str, key: str, reference: str) -> dict[str, Any] | None:
        if self.provider != "mercadopago":
            # No provider-supported unique reference search for these checkout types.
            # The caller never retries an uncertain creation automatically.
            return None
        today = datetime.now(UTC)
        rows = self._request(
            mode, key, "GET", "/v1/orders",
            params={"external_reference": reference,
                    "begin_date": (today - timedelta(days=7)).isoformat(),
                    "end_date": (today + timedelta(days=1)).isoformat(),
                    "limit": "10"},
        ).get("data")
        if not isinstance(rows, list):
            raise ProviderUncertain("Order search uncertain")
        exact = [r for r in rows if isinstance(r, dict)
                 and r.get("external_reference") == reference]
        if len(exact) > 1:
            raise ProviderUncertain("Duplicate checkout at provider")
        return self._normalize(exact[0]) if exact else None

    def create_charge(
        self, mode: str, key: str, customer: str, reference: str, cents: int, due: str,
    ) -> dict[str, Any]:
        if cents <= 0 or cents > 10_000_000:
            raise ProviderRejected("Invalid charge amount")
        if self.provider == "mercadopago":
            old = self.find_charge(mode, key, reference)
            if old:
                return old
            raw = self._request(
                mode, key, "POST", "/v1/orders",
                data={"type": "online", "processing_mode": "manual",
                      "capture_mode": "automatic_async",
                      "external_reference": reference, "total_amount": _money(cents),
                      "items": [{"title": "Pedido AtendeVendeIA", "quantity": 1,
                                 "unit_price": _money(cents)}]},
                extra_headers={"X-Idempotency-Key": str(uuid.uuid5(uuid.NAMESPACE_URL, reference))},
            )
        elif self.provider == "pagbank":
            payload: dict[str, Any] = {
                "reference_id": reference,
                "items": [{"name": "Pedido AtendeVendeIA", "quantity": 1,
                           "unit_amount": cents}],
                "payment_methods": [{"type": "PIX"}, {"type": "CREDIT_CARD"},
                                    {"type": "BOLETO"}],
                "customer_modifiable": True,
            }
            if self.webhook_url:
                payload["notification_urls"] = [self.webhook_url]
                payload["payment_notification_urls"] = [self.webhook_url]
            raw = self._request(mode, key, "POST", "/checkouts", data=payload)
        elif self.provider == "pagarme":
            raw = self._request(mode, key, "POST", "/paymentlinks",
                data={"type": "order", "order_code": reference, "max_paid_sessions": 1,
                      "max_sessions": 1, "expires_in": 2880,
                      "name": "Pedido AtendeVendeIA " + reference[-8:],
                      "cart_settings": {
                          "items": [{"name": "Pedido AtendeVendeIA",
                                     "amount": cents, "default_quantity": 1}]},
                      "payment_settings": {
                          "accepted_payment_methods": ["pix", "credit_card"],
                          "pix_settings": {},
                          "credit_card_settings": {
                              "operation_type": "auth_and_capture",
                              "installments": [{"number": 1, "total": cents}],
                          }},
                })
        else:
            token = self._token(mode, key)
            raw = self._request(mode, token, "POST", self._picpath(mode) + "/create",
                data={"charge": {
                    "name": "Pedido AtendeVendeIA", "description": reference,
                    "order_number": gateway_ref(reference, self.provider),
                    "payment": {"methods": ["BRCODE", "CREDIT_CARD"],
                                "brcode_arrangements": ["PICPAY", "PIX"]},
                    "amounts": {"product": cents, "delivery": 0}},
                    "options": {"allow_create_pix_key": True,
                                "card_max_installment_number": 1}},
            )
        return self._normalize(raw, reference=reference)

    def _picpath(self, mode: str) -> str:
        return "/sandbox/v1/paymentlink" if mode == "sandbox" else "/v1/paymentlink"

    def get_charge(self, mode: str, key: str, payment_id: str) -> dict[str, Any]:
        pid = _id(payment_id)
        if self.provider == "mercadopago":
            raw = self._request(mode, key, "GET", "/v1/orders/" + pid)
        elif self.provider == "pagbank":
            if not pid.startswith("CHEC_"):
                raise ProviderRejected("Invalid PagBank checkout identifier")
            raw = self._request(mode, key, "GET", "/checkouts/" + pid,
                                params={"limit": "100"})
        elif self.provider == "pagarme":
            if not pid.startswith("pl_"):
                raise ProviderRejected("Invalid Pagar.me link identifier")
            raw = self._request(mode, key, "GET", "/paymentlinks/" + pid)
        else:
            raw = self._request(mode, self._token(mode, key), "GET",
                                self._picpath(mode) + "/" + pid)
            txs = self._request(mode, self._token(mode, key), "GET",
                                self._picpath(mode) + "/" + pid + "/transactions",
                                params={"page": "1", "perPage": "100"})
            raw["_validated_transactions"] = txs.get("transactions")
        return self._normalize(raw, payment_id=pid)

    def _normalize(
        self, row: dict[str, Any], *, reference: str | None = None,
        payment_id: str | None = None,
    ) -> dict[str, Any]:
        p = self.provider
        if p == "mercadopago":
            pid = _id(row.get("id"))
            status, detail = row.get("status"), row.get("status_detail")
            if status == "processed" and detail == "accredited":
                canonical = "CONFIRMED"
            elif status == "refunded":
                canonical = "REFUNDED"
            elif detail == "partially_refunded":
                canonical = "PARTIALLY_REFUNDED"
            elif status in ("created", "processing", "action_required"):
                canonical = "PENDING"
            else:
                canonical = "REVIEW"
            value = row.get("total_amount")
            url = row.get("checkout_url")
            return {"id": pid, "externalReference": row.get("external_reference"),
                    "value": value, "status": canonical, "invoiceUrl": url}
        if p == "pagbank":
            pid = _id(row.get("id"))
            items = row.get("items")
            if not isinstance(items, list) or not items:
                raise ProviderUncertain("Checkout items unavailable")
            sum_cents = sum(int(x["quantity"]) * int(x["unit_amount"]) for x in items
                            if isinstance(x, dict))
            sum_cents += int(row.get("additional_amount") or 0)
            sum_cents -= int(row.get("discount_amount") or 0)
            orders = row.get("orders") or []
            charges = [ch for od in orders if isinstance(od, dict)
                       for ch in od.get("charges", []) if isinstance(ch, dict)]
            statuses = {str(ch.get("status")) for ch in charges}
            paid = sum(int(ch.get("amount", {}).get("summary", {}).get("paid", 0))
                       for ch in charges if ch.get("status") == "PAID")
            if statuses & {"REFUNDED", "PARTIALLY_REFUNDED", "CHARGEBACK"}:
                canonical = "REFUNDED" if "REFUNDED" in statuses else "PARTIALLY_REFUNDED"
            elif len(charges) == 1 and paid == sum_cents and "PAID" in statuses:
                canonical = "CONFIRMED"
            else:
                canonical = "PENDING" if not charges or statuses <= {
                    "WAITING", "AUTHORIZED", "IN_ANALYSIS", "DECLINED", "CANCELED"} else "REVIEW"
            links = row.get("links") or []
            href = next((item.get("href") for item in links
                         if isinstance(item, dict) and item.get("rel") == "PAY"), None)
            return {"id": pid, "externalReference": row.get("reference_id"),
                    "value": _money(sum_cents), "status": canonical, "invoiceUrl": href}
        if p == "pagarme":
            pid = _id(row.get("id"))
            cart = row.get("cart_settings") or {}
            items = cart.get("items") or []
            expected = sum(int(x.get("amount", 0)) * int(x.get("default_quantity", 1))
                           for x in items if isinstance(x, dict))
            paid_sessions = int(row.get("total_paid_sessions") or 0)
            if paid_sessions > 1:
                status = "REVIEW"
            elif paid_sessions == 1 and row.get("type") == "order":
                status = "CONFIRMED"
            elif row.get("status") in ("active", "building", "expired", "cancelled"):
                status = "PENDING"
            else:
                status = "REVIEW"
            return {"id": pid, "externalReference": row.get("order_code"),
                    "value": _money(expected), "status": status,
                    "invoiceUrl": row.get("url")}
        pid = payment_id or row.get("id") or row.get("paymentLinkId")
        if not pid:
            # PicPay returns link but may omit id. Extract only a strict final /p/ token.
            url = row.get("link", "")
            match = re.fullmatch(
                r"https://(?:link\.picpay\.com|link\.ppay\.me)/p/"
                r"([a-zA-Z0-9_-]{3,100})",
                url,
            )
            pid = match.group(1) if match else None
        pid = _id(pid)
        details = row.get("details") or {}
        cents = int(row.get("amount") or 0)
        txs = row.get("_validated_transactions", [])
        if txs is None:
            raise ProviderUncertain("PicPay transactions unavailable")
        if not isinstance(txs, list):
            raise ProviderUncertain("PicPay transactions invalid")
        states = {t.get("status") for t in txs if isinstance(t, dict)}
        paid = sum(int(t.get("amount", 0)) for t in txs if isinstance(t, dict)
                   and t.get("status") == "PAYED")
        if states & {"REFUNDED", "PARTREFUNDED"}:
            status = "REFUNDED" if "REFUNDED" in states else "PARTIALLY_REFUNDED"
        elif len(txs) == 1 and paid == cents:
            status = "CONFIRMED"
        else:
            status = "PENDING" if not txs or states <= {
                "DENIED", "AUTHORIZED", "PENDING", "CANCELLED"} else "REVIEW"
        return {"id": pid, "externalReference": details.get("orderNumber"),
                "value": _money(cents), "status": status, "invoiceUrl": row.get("link")}


def provider_client(
    name: str, *, asaas: AsaasClient | None = None,
    webhook_url: str | None = None, transport: httpx.BaseTransport | None = None,
) -> AsaasClient | HostedGateway:
    if name == "asaas":
        return asaas or AsaasClient(transport=transport)
    return HostedGateway(name, transport=transport, webhook_url=webhook_url)


def verified_provider_charge(
    provider: str, data: dict[str, Any], reference: str, cents: int,
    expected_id: str | None = None,
) -> str:
    """Verify ownership, exact immutable amount and provider ID before any state mutation."""
    if provider == "asaas":
        from fm_seller.merchant.asaas import verify_charge
        return verify_charge(data, reference, cents, expected_id)
    payment_id = _id(data.get("id"))
    if expected_id is not None and payment_id != expected_id:
        from fm_seller.errors import AppError
        raise AppError(409, "finance_mismatch", "Identificador divergente.")
    if data.get("externalReference") != gateway_ref(reference, provider) or (
        amount_cents(data.get("value")) != cents
    ):
        from fm_seller.errors import AppError
        raise AppError(409, "finance_mismatch", "Valor ou pedido divergente.")
    return payment_id
