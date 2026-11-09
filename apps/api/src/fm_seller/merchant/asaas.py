"""Allowlisted Asaas transport; never resend unknown financial mutations automatically."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from fm_seller.errors import AppError

HOSTS = {"sandbox": "https://api-sandbox.asaas.com/v3", "production": "https://api.asaas.com/v3"}


class ProviderUncertain(Exception):
    """Network/5xx or ambiguous response. Stop: reconcile, never blind retry."""


class ProviderRejected(Exception):
    """Definitive rejection, safe to show to staff without provider secrets."""


def amount_cents(value: Any) -> int | None:
    try:
        v = Decimal(str(value))
    except (ValueError, InvalidOperation):
        return None
    if not v.is_finite() or v < 0 or v > 1_000_000:
        return None
    cents = v * 100
    return int(cents) if cents == cents.to_integral_value() else None


class AsaasClient:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport

    def request(
        self,
        mode: str,
        key: str,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if mode not in HOSTS or not key or not path.startswith("/") or "//" in path:
            raise ProviderRejected("Provider settings invalid")
        if method not in ("GET", "POST") or not path.startswith(
            ("/myAccount/status/", "/customers", "/payments")
        ):
            raise ProviderRejected("Provider operation not permitted")
        try:
            with httpx.Client(
                timeout=httpx.Timeout(12.0, connect=4.0),
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = client.request(
                    method,
                    HOSTS[mode] + path,
                    headers={
                        "access_token": key,
                        "User-Agent": "AtendeVendeIA/1.0",
                        "Accept": "application/json",
                    },
                    params=params,
                    json=data,
                )
        except httpx.TransportError as exc:
            raise ProviderUncertain("Provider connection uncertain") from exc
        if response.status_code >= 500 or response.status_code in (408, 409, 425, 429):
            raise ProviderUncertain("Provider response uncertain")
        if response.status_code >= 400:
            raise ProviderRejected(f"Provider declined ({response.status_code})")
        try:
            answer = response.json()
        except ValueError as exc:
            raise ProviderUncertain("Unparsable provider response") from exc
        if not isinstance(answer, dict):
            raise ProviderUncertain("Unexpected provider response")
        return answer

    def verify_account(self, mode: str, key: str) -> bool:
        status = self.request(mode, key, "GET", "/myAccount/status/")
        return status.get("general") == "APPROVED"

    def customer(self, mode: str, key: str, name: str, cpf_cnpj: str, reference: str) -> str:
        found = self.request(
            mode, key, "GET", "/customers", params={"cpfCnpj": cpf_cnpj, "limit": "10"}
        )
        rows = found.get("data")
        if not isinstance(rows, list):
            raise ProviderUncertain("Customer lookup uncertain")
        exact = [
            x
            for x in rows
            if isinstance(x, dict)
            and "".join(c for c in str(x.get("cpfCnpj", "")) if c.isdigit()) == cpf_cnpj
        ]
        if len(exact) > 1:
            raise ProviderUncertain("Duplicated customer at provider: review")
        if exact:
            return str(exact[0]["id"])
        created = self.request(
            mode,
            key,
            "POST",
            "/customers",
            data={
                "name": name,
                "cpfCnpj": cpf_cnpj,
                "externalReference": reference,
                "notificationDisabled": True,
            },
        )
        result = created.get("id")
        if not isinstance(result, str) or not result.startswith("cus_"):
            raise ProviderUncertain("Customer creation uncertain")
        return result

    def find_charge(self, mode: str, key: str, reference: str) -> dict[str, Any] | None:
        found = self.request(
            mode,
            key,
            "GET",
            "/payments",
            params={"externalReference": reference, "limit": "10"},
        )
        rows = found.get("data")
        if not isinstance(rows, list):
            raise ProviderUncertain("Charge lookup uncertain")
        exact = [r for r in rows if isinstance(r, dict) and r.get("externalReference") == reference]
        if len(exact) > 1:
            raise ProviderUncertain("Duplicate charges at provider: manual review")
        return exact[0] if exact else None

    def create_charge(
        self, mode: str, key: str, customer: str, reference: str, cents: int, due: str
    ) -> dict[str, Any]:
        existing = self.find_charge(mode, key, reference)
        if existing is not None:
            return existing
        return self.request(
            mode,
            key,
            "POST",
            "/payments",
            data={
                "customer": customer,
                "billingType": "UNDEFINED",
                "value": float(Decimal(cents) / 100),
                "dueDate": due,
                "description": "AtendeVendeIA " + reference[-12:],
                "externalReference": reference,
            },
        )

    def get_charge(self, mode: str, key: str, payment_id: str) -> dict[str, Any]:
        if not payment_id.startswith("pay_") or len(payment_id) > 100:
            raise ProviderRejected("Invalid payment identifier")
        return self.request(mode, key, "GET", "/payments/" + payment_id)


def safe_invoice_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 500:
        raise ProviderUncertain("Invalid invoice link")
    parsed = httpx.URL(value)
    if parsed.scheme != "https" or parsed.host not in (
        "www.asaas.com",
        "asaas.com",
        "sandbox.asaas.com",
        "www.asaas.com.br",
    ):
        raise ProviderUncertain("Invalid provider payment host")
    return value


def verify_charge(
    data: dict[str, Any], reference: str, cents: int, expected_id: str | None = None
) -> str:
    payment_id = data.get("id")
    if not isinstance(payment_id, str) or not payment_id.startswith("pay_"):
        raise ProviderUncertain("Provider did not return a payment ID")
    if expected_id is not None and payment_id != expected_id:
        raise AppError(409, "finance_mismatch", "Identificador da cobrança divergente.")
    if data.get("externalReference") != reference or amount_cents(data.get("value")) != cents:
        raise AppError(409, "finance_mismatch", "Valor ou pedido divergente. Revisão obrigatória.")
    return payment_id
