"""Real PG/RLS regression: delivery quote, conversational consent and Asaas job."""

from __future__ import annotations

import uuid
from typing import Any, cast

from fastapi.testclient import TestClient

from fm_seller.channels.whatsapp import upsert_conversation
from fm_seller.db import Database
from fm_seller.merchant.chat import handle_cart
from fm_seller.merchant.chat_worker import process_chat_charge_jobs
from tests.conftest import Env, connect_whatsapp
from tests.test_merchant_orders import FAKE_CPF, FakeAsaas, new_offer
from tests.test_merchant_orders import scope as scope

PHONE = "5511999556622"
POSTAL = "01001000"


def _address() -> dict[str, str]:
    return {
        "postal_code": POSTAL,
        "street": "Praça da Sé",
        "street_number": "100",
        "neighborhood": "Sé",
        "city": "São Paulo",
        "state": "SP",
        "complement": "Apto 3",
        "reference": "",
    }


def _zone(
    client: TestClient,
    name: str = "Centro",
    first: str = "01000000",
    last: str = "01099999",
    cents: int = 950,
) -> dict[str, Any]:
    response = client.post(
        "/v1/merchant/delivery/zones",
        json={"name": name, "start": first, "end": last, "fee_cents": cents},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def test_delivery_quote_and_snapshot_security(
    client: TestClient,
    env: Env,
    scope: tuple[str, FakeAsaas],
) -> None:
    _tenant, _fake = scope
    pizza = new_offer(client, "Pizza Calabresa Grande", 5990)
    order_input = {
        "idempotency_key": "delivery-secure-123456",
        "customer_name": "Cliente Sintético",
        "customer_phone": PHONE,
        "items": [{"offer_id": pizza, "quantity": 2}],
        "method": "delivery",
        "address": _address(),
        "total_cents": 10,
    }
    missing = client.post("/v1/merchant/orders", json=order_input)
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "delivery_outside_area"
    _zone(client)
    created = client.post("/v1/merchant/orders", json=order_input)
    assert created.status_code == 200, created.text
    data = created.json()
    assert data["total_cents"] == 12930
    assert data["delivery_fee_cents"] == 950
    assert data["fulfillment"]["method"] == "delivery"
    assert data["fulfillment"]["postal_code"] == POSTAL
    assert data["fulfillment"]["street_number"] == "100"
    assert client.post("/v1/merchant/orders", json=order_input).json()["id"] == data["id"]
    changed = dict(order_input)
    changed["address"] = {**_address(), "street_number": "101"}
    assert client.post("/v1/merchant/orders", json=changed).status_code == 409

    overlap = client.post(
        "/v1/merchant/delivery/zones",
        json={
            "name": "Cobertura duplicada",
            "start": "01001000",
            "end": "01020000",
            "fee_cents": 800,
        },
    )
    assert overlap.status_code == 409
    assert client.get("/v1/merchant/delivery/zones").json()[0]["fee_cents"] == 950
    # Changing zone after order never changes the financial snapshot.
    assert client.delete("/v1/merchant/delivery/zones/" + _zone_id(client)).status_code == 200
    assert client.get("/v1/merchant/orders/" + data["id"]).json()["total_cents"] == 12930
    other_email = env.sql("SELECT customer_email FROM merchant_orders WHERE id=%s", (data["id"],))
    assert other_email == [("",)]


def _zone_id(client: TestClient) -> str:
    return str(client.get("/v1/merchant/delivery/zones").json()[0]["id"])


def test_address_missing_and_invalid_zone_refuse_money(
    client: TestClient,
    scope: tuple[str, FakeAsaas],
) -> None:
    _, fake = scope
    p = new_offer(client, "Pizza Teste", 3990)
    _zone(client)
    payload: dict[str, Any] = {
        "idempotency_key": "delivery-invalid-abc123",
        "customer_name": "Cliente",
        "method": "delivery",
        "items": [{"offer_id": p, "quantity": 1}],
        "address": {**_address(), "street_number": ""},
    }
    res = client.post("/v1/merchant/orders", json=payload)
    assert res.status_code == 400
    assert fake.charge_calls == 0
    payload["address"] = {**_address(), "postal_code": "99999999"}
    assert client.post("/v1/merchant/orders", json=payload).status_code == 409
    pickup = {**payload, "method": "pickup", "address": {}}
    success = client.post("/v1/merchant/orders", json=pickup)
    assert success.status_code == 200
    assert success.json()["total_cents"] == 3990
    assert success.json()["delivery_fee_cents"] == 0


def test_conversational_cart_safely_collects_and_sends_unique_link(
    client: TestClient,
    env: Env,
    db: Database,
    scope: tuple[str, FakeAsaas],
) -> None:
    tenant_str, fake = scope
    tid = uuid.UUID(tenant_str)
    new_offer(client, "Pizza Calabresa Grande", 5990)
    _zone(client)
    connect_whatsapp(client, env, tenant_str)
    env.sql(
        "INSERT INTO tenant_settings(tenant_id,ai_enabled) VALUES (%s,true) "
        "ON CONFLICT(tenant_id) DO UPDATE SET ai_enabled=true",
        (tenant_str,),
    )
    with db.tx(tenant_id=tid) as conn:
        _, cid = upsert_conversation(conn, tid, PHONE, "Cliente")
        conn.execute(
            "UPDATE conversations SET last_inbound_at=now(),status='bot' WHERE id=%s",
            (cid,),
        )
    offers = client.get("/v1/seller/offers").json()

    def step(text: str) -> str:
        with db.tx(tenant_id=tid) as conn:
            message = conn.execute(
                "INSERT INTO messages(tenant_id,conversation_id,direction,author,"
                "body,status,handled) VALUES(%s,%s,'in','customer',%s,'received',true)"
                "RETURNING id",
                (tid, cid, text),
            ).fetchone()
            assert message
            response = handle_cart(
                conn,
                tid,
                cid,
                "Cliente",
                PHONE,
                text,
                message["id"],
                offers,
                env.box,
            )
        assert response is not None
        return response

    assert "entrega ou retirada" in step("Quero 2 Pizza Calabresa Grande")
    assert "CEP" in step("entrega")
    assert "rua" in step(POSTAL).lower()
    assert "número" in step("Praça da Sé").lower()
    assert "bairro" in step("100").lower()
    assert "cidade" in step("Sé").lower()
    assert "UF" in step("São Paulo")
    assert "complemento" in step("SP").lower()
    assert "nome" in step("Apto 3").lower()
    summary = step("Cliente")
    assert "R$" in summary and "2x Pizza Calabresa Grande" in summary
    assert POSTAL in summary
    confirmed = step("confirmo")
    assert "CPF ou CNPJ" in confirmed
    queued = step(FAKE_CPF)
    assert "cobrança individual" in queued
    assert env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s ORDER BY created_at DESC LIMIT 1",
        (cid,),
    ) == [("[DOCUMENTO FISCAL PROTEGIDO]",)]
    assert env.sql("SELECT status,total_cents FROM merchant_orders WHERE tenant_id=%s", (tid,)) == [
        ("pending", 12930)
    ]
    stats = process_chat_charge_jobs(db, env.box, env.settings(), provider=fake)
    assert stats["ready"] == 1
    assert fake.charge_calls == 1
    assert env.sql(
        "SELECT state,tax_id_encrypted IS NULL FROM merchant_chat_charge_jobs WHERE tenant_id=%s",
        (tid,),
    ) == [("done", True)]
    count = env.sql(
        "SELECT count(*) FROM messages WHERE tenant_id=%s AND conversation_id=%s "
        "AND author='bot' AND body LIKE 'Seu pedido está registrado. Pague%%'",
        (tid, cid),
    )[0][0]
    assert count == 1
    process_chat_charge_jobs(db, env.box, env.settings(), provider=fake)
    assert fake.charge_calls == 1
    assert env.sql(
        "SELECT count(*) FROM messages WHERE tenant_id=%s AND conversation_id=%s "
        "AND author='bot' AND body LIKE 'Seu pedido está registrado. Pague%%'",
        (tid, cid),
    ) == [(1,)]


def test_chat_rejects_unsupported_postal_and_ambiguous_products(
    client: TestClient,
    env: Env,
    db: Database,
    scope: tuple[str, FakeAsaas],
) -> None:
    tenant_str, _ = scope
    tenant = uuid.UUID(tenant_str)
    new_offer(client, "Pizza Calabresa Grande", 5990)
    new_offer(client, "Pizza Calabresa Broto", 3790)
    with db.tx(tenant_id=tenant) as conn:
        _, cid = upsert_conversation(conn, tenant, PHONE, "Cliente")
    offers = client.get("/v1/seller/offers").json()

    def step(text: str) -> str | None:
        with db.tx(tenant_id=tenant) as conn:
            return handle_cart(
                conn,
                tenant,
                cid,
                "Cliente",
                PHONE,
                text,
                uuid.uuid4(),
                offers,
                None,
            )

    assert "Opções cadastradas" in (step("Quero pizza calabresa") or "")
    assert "entrega ou retirada" in (step("Pizza Calabresa Broto") or "")
    assert "CEP" in (step("entrega") or "")
    assert "não consta" in (step("99999999") or "")
    assert "CEP" in (step("retirada") or "") or "não consta" in (step("retirada") or "")
    assert env.sql("SELECT count(*) FROM merchant_orders WHERE tenant_id=%s", (tenant,)) == [(0,)]
