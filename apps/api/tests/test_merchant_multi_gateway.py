"""Multi-provider merchant acceptance: tenant isolation and server-side payment authority."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from fm_seller.db import Database
from fm_seller.merchant.worker import process_payment_events
from tests.conftest import Env, login, unique_email
from tests.test_merchant_orders import FAKE_CPF, KEY, WEBHOOK, FakeAsaas, new_offer
from tests.test_merchant_orders import scope as scope

OTHER_KEY = "APP_USR-merchant-test-key-" + "A" * 28
OTHER_SECRET = "mp-secret-test-" + "B" * 40


class FakeHosted:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.status = "PENDING"
        self.charge_calls = 0

    def verify_account(self, mode: str, key: str) -> bool:
        return mode in ("sandbox", "production") and key == OTHER_KEY

    def customer(self, mode: str, key: str, name: str, cpf_cnpj: str, reference: str) -> str:
        assert key == OTHER_KEY
        return cpf_cnpj

    def create_charge(
        self,
        mode: str,
        key: str,
        customer: str,
        reference: str,
        cents: int,
        due: str,
    ) -> dict[str, Any]:
        assert key == OTHER_KEY and customer == FAKE_CPF
        self.charge_calls += 1
        pid = "ORDTEST" + reference[-12:].replace("-", "")
        row: dict[str, Any] = {
            "id": pid,
            "value": cents / 100,
            "externalReference": reference,
            "status": "PENDING",
            "invoiceUrl": "https://www.mercadopago.com.br/checkout/v1/redirect?order_id=" + pid,
        }
        self.rows[pid] = row
        return row

    def get_charge(self, mode: str, key: str, payment_id: str) -> dict[str, Any]:
        assert key == OTHER_KEY
        row = dict(self.rows[payment_id])
        row["status"] = self.status
        return row

    def find_charge(self, mode: str, key: str, reference: str) -> dict[str, Any] | None:
        return next((x for x in self.rows.values() if x["externalReference"] == reference), None)


def test_provider_switch_keeps_historical_account_and_verifies_mp_webhook(
    client: TestClient,
    env: Env,
    db: Database,
    scope: tuple[str, FakeAsaas],
    monkeypatch: Any,
) -> None:
    tenant, old_asaas = scope
    fake = FakeHosted()
    from fm_seller.merchant import service as service_module
    from fm_seller.merchant.gateways import provider_client as factory
    monkeypatch.setattr(
        service_module,
        "provider_client",
        lambda name, asaas=None, webhook_url=None: (
            fake if name == "mercadopago" else factory(name, asaas=asaas, webhook_url=webhook_url)
        ),
    )

    account = client.put(
        "/v1/merchant/account",
        json={
            "provider": "mercadopago",
            "mode": "production",
            "access_token": OTHER_KEY,
            "webhook_token": OTHER_SECRET,
            "delivery_fee_cents": 500,
        },
    )
    assert account.status_code == 200, account.text
    info = account.json()
    assert info["provider"] == "mercadopago"
    assert len(info["accounts"]) == 2
    assert {x["provider"] for x in info["accounts"]} == {"asaas", "mercadopago"}
    assert OTHER_KEY not in str(info)
    assert OTHER_SECRET not in str(info)

    offer = new_offer(client, "Pizza multi PSP", 5000)
    created = client.post(
        "/v1/merchant/orders",
        json={
            "idempotency_key": "multi-merchant-order-12345",
            "customer_name": "Comprador Sintético",
            "customer_phone": "5511999888777",
            "items": [{"offer_id": offer, "quantity": 2}],
        },
    )
    assert created.status_code == 200, created.text
    oid = created.json()["id"]
    response = client.post(
        f"/v1/merchant/orders/{oid}/charge", json={"customer_cpf_cnpj": FAKE_CPF}
    )
    assert response.status_code == 200, response.text
    assert response.json()["charge"]["provider"] == "mercadopago"
    pid = next(iter(fake.rows))
    assert fake.charge_calls == 1

    ts = str(int(datetime.now(UTC).timestamp()))
    request_id = str(uuid.uuid4())
    manifest = f"id:{pid};request-id:{request_id};ts:{ts};"
    digest = hmac.new(OTHER_SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    webhook = info["webhook_url"].removeprefix("https://api.example.test")
    body = {"id": "event_123", "type": "order", "data": {"id": pid}}
    bad = client.post(
        webhook + "?data.id=" + pid,
        headers={"x-signature": "ts=" + ts + ",v1=" + "0" * 64, "x-request-id": request_id},
        content=json.dumps(body),
    )
    assert bad.status_code == 401
    assert client.get(f"/v1/merchant/orders/{oid}").json()["status"] == "awaiting_payment"

    good = client.post(
        webhook + "?data.id=" + pid,
        headers={"x-signature": f"ts={ts},v1={digest}", "x-request-id": request_id},
        content=json.dumps(body),
    )
    assert good.status_code == 200, good.text
    assert good.json() == {"status": "accepted"}
    fake.status = "CONFIRMED"
    out = process_payment_events(db, env.box, env.settings())
    assert out.processed >= 1
    assert client.get(f"/v1/merchant/orders/{oid}").json()["status"] == "paid"
    assert fake.charge_calls == 1

    # Active provider may be switched, but old provider credentials remain usable
    # for historical reconciliations and refunds.
    back = client.put(
        "/v1/merchant/account",
        json={
            "provider": "asaas",
            "mode": "sandbox",
            "access_token": KEY,
            "webhook_token": WEBHOOK,
        },
    )
    assert back.status_code == 200, back.text
    assert back.json()["provider"] == "asaas"
    assert len(back.json()["accounts"]) == 2
    fake.status = "REFUNDED"
    reconciled = process_payment_events(db, env.box, env.settings())
    assert reconciled.processed == 0  # No invented second webhook
    other_payment = client.post(f"/v1/merchant/orders/{oid}/reconcile")
    assert other_payment.status_code == 200, other_payment.text
    assert other_payment.json()["status"] == "refunded"
    assert old_asaas.charge_calls == 0

    other_email = unique_email("multi-tenant-separate")
    other = env.tenant("Loja sem MP", other_email)
    assert login(client, other_email).status_code == 200
    assert client.get(f"/v1/merchant/orders/{oid}").status_code == 404
    assert client.get("/v1/merchant/account").json()["configured"] is False
    assert other != tenant
