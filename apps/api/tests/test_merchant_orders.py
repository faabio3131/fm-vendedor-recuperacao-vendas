"""Financial invariants: price snapshots, payment authority, isolation, replay and refunds."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from fm_seller.db import Database
from fm_seller.merchant.asaas import AsaasClient, ProviderUncertain
from fm_seller.merchant.worker import process_payment_events
from tests.conftest import Env, login, unique_email

FAKE_CPF = "52998224725"  # synthetic structurally valid CPF
KEY = "sandbox-api-token-" + "a" * 34
WEBHOOK = "testing-asas-webhook-secret-" + "x" * 38
ORDER_BASE = "/v1/merchant/orders"


class FakeAsaas(AsaasClient):
    """External Asaas adapter simulation: never contacts a financial provider."""

    def __init__(self) -> None:
        self.payments: dict[str, dict[str, Any]] = {}
        self.customer_calls = 0
        self.charge_calls = 0
        self.uncertain = False
        self.verified = True

    def verify_account(self, mode: str, key: str) -> bool:
        assert mode in ("sandbox", "production") and key == KEY
        return self.verified

    def customer(self, mode: str, key: str, name: str, cpf_cnpj: str, reference: str) -> str:
        assert cpf_cnpj == FAKE_CPF
        self.customer_calls += 1
        return "cus_SYNTHETIC"

    def find_charge(self, mode: str, key: str, reference: str) -> dict[str, Any] | None:
        return next(
            (x for x in self.payments.values() if x["externalReference"] == reference),
            None,
        )

    def create_charge(
        self, mode: str, key: str, customer: str, reference: str, cents: int, due: str
    ) -> dict[str, Any]:
        self.charge_calls += 1
        result: dict[str, Any] = {
            "id": "pay_" + reference[-36:].replace("-", "")[:24],
            "value": cents / 100,
            "externalReference": reference,
            "status": "PENDING",
            "invoiceUrl": "https://sandbox.asaas.com/i/synthetic",
        }
        self.payments[result["id"]] = result
        if self.uncertain:
            raise ProviderUncertain("synthetic timeout after creation")
        return result

    def get_charge(self, mode: str, key: str, payment_id: str) -> dict[str, Any]:
        return self.payments[payment_id]


@pytest.fixture
def scope(client: TestClient, env: Env) -> tuple[str, FakeAsaas]:
    tenant = env.tenant("Pizzaria Sintética", unique_email("merchant"))
    owner = env.sql("SELECT email FROM pending_invites WHERE tenant_id=%s LIMIT 1", (tenant,))[0][0]
    assert login(client, owner).status_code == 200
    fake = FakeAsaas()
    cast(Any, client.app).state.merchant_provider = fake
    result = client.put(
        "/v1/merchant/account",
        json={
            "mode": "sandbox",
            "access_token": KEY,
            "webhook_token": WEBHOOK,
            "delivery_fee_cents": 500,
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["verified"] is True
    return tenant, fake


def new_offer(client: TestClient, name: str, price: int) -> str:
    data = client.post(
        "/v1/seller/offers",
        json={
            "name": name,
            "description": "Sintético",
            "price_cents": price,
            "payment_url": "https://checkout.example.test/preview",
        },
    )
    assert data.status_code == 200, data.text
    return str(data.json()["id"])


def order(
    client: TestClient,
    offer: str,
    key: str = "pedido-sintetico-123456",
    qty: int = 2,
    name: str = "João Sintético",
) -> dict[str, Any]:
    r = client.post(
        ORDER_BASE,
        json={
            "idempotency_key": key,
            "customer_name": name,
            "customer_phone": "5511999887766",
            "items": [{"offer_id": offer, "quantity": qty}],
            "total_cents": 1,  # malicious suggestion: ignored; DB determines actual amount
        },
    )
    assert r.status_code == 200, r.text
    return cast(dict[str, Any], r.json())


def charge(client: TestClient, oid: str) -> dict[str, Any]:
    r = client.post(
        f"{ORDER_BASE}/{oid}/charge",
        json={"customer_cpf_cnpj": FAKE_CPF},
    )
    assert r.status_code == 200, r.text
    return cast(dict[str, Any], r.json())


def event(
    client: TestClient,
    account: dict[str, Any],
    eid: str,
    payment_id: str,
    kind: str,
    token: str = WEBHOOK,
) -> Any:
    path = account["webhook_url"].removeprefix("https://api.example.test")
    return client.post(
        path,
        headers={"asaas-access-token": token},
        json={"id": eid, "event": kind, "payment": {"id": payment_id}},
    )


def test_finance_price_snapshot_duplicate_key_and_tenant_role(
    client: TestClient,
    env: Env,
    scope: tuple[str, FakeAsaas],
) -> None:
    _, _fake = scope
    pizza = new_offer(client, "Calabresa", 5990)
    first = order(client, pizza)
    assert first["total_cents"] == 12480
    assert first["items"][0]["unit_price_cents"] == 5990
    assert order(client, pizza)["id"] == first["id"]
    changed = client.post(
        ORDER_BASE,
        json={
            "idempotency_key": "pedido-sintetico-123456",
            "customer_name": "Outro comprador",
            "items": [{"offer_id": pizza, "quantity": 1}],
        },
    )
    assert changed.status_code == 409
    # A catalog change cannot rewrite historical order values.
    env.sql("UPDATE offers SET price_cents=9999 WHERE id=%s", (pizza,))
    assert client.get(f"{ORDER_BASE}/{first['id']}").json()["total_cents"] == 12480
    two = order(client, pizza, key="pedido-novo-345678123456", qty=1)
    assert two["total_cents"] == 10499
    second_user = unique_email("merchant-other")
    env.tenant("Outra Loja", second_user)
    assert login(client, second_user).status_code == 200
    assert client.get(f"{ORDER_BASE}/{first['id']}").status_code == 404
    assert client.get(ORDER_BASE).json() == []
    assert client.post(
        ORDER_BASE,
        json={
            "idempotency_key": "outro-pedido-123456",
            "customer_name": "Teste",
            "items": [{"offer_id": pizza, "quantity": 1}],
        },
    ).status_code in (400, 404)


def test_cannot_charge_twice_and_webhook_only_queues(
    client: TestClient,
    env: Env,
    db: Database,
    scope: tuple[str, FakeAsaas],
) -> None:
    _, fake = scope
    pizza = new_offer(client, "Portuguesa", 4990)
    created = order(client, pizza, key="pagamento-1234567890")
    result = charge(client, created["id"])
    assert result["status"] == "awaiting_payment"
    assert result["charge"]["invoice_url"].startswith("https://sandbox.asaas.com/")
    assert fake.charge_calls == 1
    repeat = client.post(
        f"{ORDER_BASE}/{created['id']}/charge",
        json={"customer_cpf_cnpj": FAKE_CPF},
    )
    assert repeat.status_code == 409 and fake.charge_calls == 1
    account = client.get("/v1/merchant/account").json()
    pid = next(iter(fake.payments))
    spoof = event(client, account, "evt_001", pid, "PAYMENT_RECEIVED", token="wrong")
    assert spoof.status_code == 401
    accepted = event(client, account, "evt_001", pid, "PAYMENT_RECEIVED")
    assert accepted.status_code == 200, accepted.text
    assert client.get(f"{ORDER_BASE}/{created['id']}").json()["status"] == "awaiting_payment"
    assert event(client, account, "evt_001", pid, "PAYMENT_RECEIVED").json() == {
        "status": "duplicate"
    }
    # Provider still says pending; a received-looking webhook MUST NOT mark paid.
    processed = process_payment_events(db, env.box, env.settings(), client=fake)
    assert processed.processed == 1
    assert client.get(f"{ORDER_BASE}/{created['id']}").json()["status"] == "awaiting_payment"
    fake.payments[pid]["status"] = "CONFIRMED"
    assert event(client, account, "evt_002", pid, "PAYMENT_CONFIRMED").status_code == 200
    process_payment_events(db, env.box, env.settings(), client=fake)
    paid = client.get(f"{ORDER_BASE}/{created['id']}").json()
    assert paid["status"] == "paid" and paid["paid_at"] is not None
    assert paid["settled_at"] is None
    fake.payments[pid]["status"] = "RECEIVED"
    assert event(client, account, "evt_003", pid, "PAYMENT_RECEIVED").status_code == 200
    process_payment_events(db, env.box, env.settings(), client=fake)
    assert client.get(f"{ORDER_BASE}/{created['id']}").json()["settled_at"] is not None
    fake.payments[pid]["status"] = "REFUNDED"
    assert event(client, account, "evt_004", pid, "PAYMENT_REFUNDED").status_code == 200
    process_payment_events(db, env.box, env.settings(), client=fake)
    assert client.get(f"{ORDER_BASE}/{created['id']}").json()["status"] == "refunded"
    # No regression into paid from a stale event.
    fake.payments[pid]["status"] = "RECEIVED"
    assert event(client, account, "evt_005", pid, "PAYMENT_RECEIVED").status_code == 200
    process_payment_events(db, env.box, env.settings(), client=fake)
    assert client.get(f"{ORDER_BASE}/{created['id']}").json()["status"] == "refunded"


def test_financial_value_mismatch_fails_closed(
    client: TestClient,
    env: Env,
    db: Database,
    scope: tuple[str, FakeAsaas],
) -> None:
    _, fake = scope
    offer = new_offer(client, "Mussarela", 4800)
    made = charge(client, order(client, offer, key="mismatch-1234567890")["id"])
    pid = next(iter(fake.payments))
    fake.payments[pid]["value"] = 0.01
    fake.payments[pid]["status"] = "RECEIVED"
    a = client.get("/v1/merchant/account").json()
    assert event(client, a, "evt_mismatch_1", pid, "PAYMENT_RECEIVED").status_code == 200
    process_payment_events(db, env.box, env.settings(), client=fake)
    assert client.get(f"{ORDER_BASE}/{made['id']}").json()["status"] == "review"


def test_timeout_does_not_create_second_charge(
    client: TestClient,
    env: Env,
    scope: tuple[str, FakeAsaas],
) -> None:
    _, fake = scope
    offer = new_offer(client, "Marguerita", 4500)
    made = order(client, offer, key="uncertain-1234567890")
    fake.uncertain = True
    r = client.post(
        f"{ORDER_BASE}/{made['id']}/charge",
        json={"customer_cpf_cnpj": FAKE_CPF},
    )
    assert r.status_code == 503
    assert fake.charge_calls == 1
    assert client.get(f"{ORDER_BASE}/{made['id']}").json()["status"] == "charging"
    assert (
        client.post(
            f"{ORDER_BASE}/{made['id']}/charge", json={"customer_cpf_cnpj": FAKE_CPF}
        ).status_code
        == 409
    )
    assert fake.charge_calls == 1
    # Reconcile retrieves preexisting charge by unique externalReference.
    fake.uncertain = False
    r2 = client.post(f"{ORDER_BASE}/{made['id']}/reconcile")
    assert r2.status_code == 200, r2.text
    assert fake.charge_calls == 1
    assert r2.json()["status"] == "awaiting_payment"


def test_invalid_customer_document_and_wrong_auth(
    client: TestClient, env: Env, scope: tuple[str, FakeAsaas]
) -> None:
    _, fake = scope
    product = new_offer(client, "Bebida", 1000)
    made = order(client, product, key="bad-document-1234567890")
    assert (
        client.post(
            f"{ORDER_BASE}/{made['id']}/charge",
            json={"customer_cpf_cnpj": "11111111111"},
        ).status_code
        == 400
    )
    assert fake.charge_calls == 0
    a = client.get("/v1/merchant/account").json()
    path = a["webhook_url"].removeprefix("https://api.example.test")
    assert (
        client.post(
            path,
            headers={"asaas-access-token": WEBHOOK},
            content=json.dumps(
                {"id": "evt_x", "event": "PAYMENT_RECEIVED", "payment": {"id": "foo"}}
            ).encode(),
        ).status_code
        == 400
    )
    # Account settings never expose keys.
    assert KEY not in str(a) and WEBHOOK not in str(a)


def test_account_rotation_denied_with_pending_payments(
    client: TestClient, scope: tuple[str, FakeAsaas]
) -> None:
    _, _fake = scope
    offer = new_offer(client, "Pastel", 1250)
    made = order(client, offer, key="rotate-123456789012")
    charge(client, made["id"])
    r = client.put(
        "/v1/merchant/account",
        json={
            "mode": "sandbox",
            "access_token": KEY,
            "webhook_token": WEBHOOK + "b",
            "delivery_fee_cents": 500,
        },
    )
    assert r.status_code == 409


def test_provider_unapproved_does_not_save_credentials(
    client: TestClient,
    env: Env,
) -> None:
    tenant = env.tenant("Conta ainda não aprovada", unique_email("pending-merchant"))
    email = env.sql("SELECT email FROM pending_invites WHERE tenant_id=%s LIMIT 1", (tenant,))[0][0]
    login(client, email)
    fake = FakeAsaas()
    fake.verified = False
    cast(Any, client.app).state.merchant_provider = fake
    r = client.put(
        "/v1/merchant/account",
        json={"mode": "production", "access_token": KEY, "webhook_token": WEBHOOK},
    )
    assert r.status_code == 409
    assert client.get("/v1/merchant/account").json()["configured"] is False
