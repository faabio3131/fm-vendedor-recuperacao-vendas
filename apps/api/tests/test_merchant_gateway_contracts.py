"""Transport contracts against synthetic HTTP providers, without charging money."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from fm_seller.errors import AppError
from fm_seller.merchant.asaas import ProviderUncertain
from fm_seller.merchant.gateways import (
    HostedGateway,
    gateway_ref,
    payment_url,
    verified_provider_charge,
)

REF = "av-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
AMOUNT = 1234


@pytest.mark.parametrize("provider", ["mercadopago", "pagbank", "pagarme", "picpay"])
def test_hosted_checkout_requires_exact_provider_order_and_server_payment(
    provider: str,
) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append((request.method, path))
        if path == "/oauth2/token":
            data = json.loads(request.content)
            assert data["grant_type"] == "client_credentials"
            return httpx.Response(200, json={"access_token": "fake_oauth_token_123456"})
        if path == "/users/me":
            return httpx.Response(200, json={"id": 12345})
        if path == "/public-keys":
            return httpx.Response(200, json={"public_key": "fake-webhook-key"})
        if path.endswith("/paymentlinks") and request.method == "GET":
            return httpx.Response(200, json={"data": []})
        if path == "/v1/orders" and request.method == "GET":
            return httpx.Response(200, json={"data": []})
        if path.endswith("/transactions"):
            return httpx.Response(
                200,
                json={
                    "transactions": [
                        {"transactionId": "tx123", "status": "PAYED", "amount": AMOUNT}
                    ],
                },
            )
        if provider == "mercadopago":
            payload = {
                "id": "ORDTEST123",
                "external_reference": REF,
                "total_amount": "12.34",
                "total_paid_amount": "12.34",
                "status": "processed" if request.method == "GET" else "created",
                "status_detail": "accredited" if request.method == "GET" else "created",
                "checkout_url": "https://www.mercadopago.com.br/checkout/v1/redirect?order_id=ORDTEST123",
            }
            if request.method == "POST":
                sent = json.loads(request.content)
                assert sent["processing_mode"] == "manual"
                assert sent["total_amount"] == "12.34"
                assert request.headers.get("x-idempotency-key")
            return httpx.Response(200, json=payload)
        if provider == "pagbank":
            payload: dict[str, Any] = {
                "id": "CHEC_123456789",
                "reference_id": REF,
                "items": [{"name": "Teste", "quantity": 1, "unit_amount": AMOUNT}],
                "links": [
                    {"rel": "PAY", "href": "https://pagamento.pagbank.com.br/pagamento?code=ABC"}
                ],
                "orders": [
                    {
                        "charges": [
                            {
                                "id": "CHAR_123",
                                "status": "PAID",
                                "amount": {"value": AMOUNT, "summary": {"paid": AMOUNT}},
                            }
                        ]
                    }
                ]
                if request.method == "GET"
                else [],
            }
            if request.method == "POST":
                sent = json.loads(request.content)
                assert sent["items"][0]["unit_amount"] == AMOUNT
            return httpx.Response(200, json=payload)
        if provider == "pagarme":
            payload = {
                "id": "pl_123456789",
                "order_code": REF,
                "type": "order",
                "status": "active",
                "total_paid_sessions": 1 if request.method == "GET" else 0,
                "cart_settings": {
                    "items": [{"amount": AMOUNT, "default_quantity": 1}],
                },
                "url": "https://payment-link.pagar.me/pl_123456789",
            }
            if request.method == "POST":
                sent = json.loads(request.content)
                assert sent["max_paid_sessions"] == 1
                assert sent["cart_settings"]["items"][0]["amount"] == AMOUNT
            return httpx.Response(200, json=payload)
        sent = json.loads(request.content) if request.method == "POST" else {}
        if request.method == "POST":
            assert sent["charge"]["order_number"] == gateway_ref(REF, "picpay")
            assert sent["charge"]["amounts"]["product"] == AMOUNT
        return httpx.Response(
            200,
            json={
                "id": "pplink123",
                "amount": AMOUNT,
                "details": {"orderNumber": gateway_ref(REF, "picpay")},
                "link": "https://link.picpay.com/p/pplink123",
            },
        )

    key = "clientid123:secret123456789" if provider == "picpay" else "fake_merchant_key_123456"
    gateway = HostedGateway(provider, transport=httpx.MockTransport(handler))
    assert gateway.verify_account("sandbox", key)
    before = gateway.create_charge(
        "sandbox",
        key,
        "00000000000",
        REF,
        AMOUNT,
        "2026-11-20",
    )
    assert before["status"] == "PENDING"
    assert verified_provider_charge(provider, before, REF, AMOUNT) == before["id"]
    assert payment_url(provider, before["invoiceUrl"]) == before["invoiceUrl"]
    after = gateway.get_charge("sandbox", key, before["id"])
    assert after["status"] == "CONFIRMED"
    assert verified_provider_charge(provider, after, REF, AMOUNT, before["id"]) == before["id"]
    with pytest.raises(AppError):
        verified_provider_charge(provider, after, REF, AMOUNT + 1, before["id"])
    with pytest.raises(ProviderUncertain):
        payment_url(provider, "https://malicious.example/steal")
    assert any(method == "POST" for method, _ in seen)
    assert any(method == "GET" for method, _ in seen)
