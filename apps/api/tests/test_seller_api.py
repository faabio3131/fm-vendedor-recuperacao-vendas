from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from fm_seller.channels.whatsapp import upsert_conversation
from fm_seller.db import Database
from tests.conftest import Env, login, unique_email

OFFER = {
    "name": "Curso Y",
    "description": "Aprenda Y",
    "price_cents": 29700,
    "payment_url": "https://pay.example.test/y",
}


def _owner(client: TestClient, env: Env) -> str:
    email = unique_email("sapi")
    tid = env.tenant("Loja Vendedor API", email)
    assert login(client, email).status_code == 200
    return tid


def test_offer_crud_validation_and_audit(client: TestClient, env: Env) -> None:
    tid = _owner(client, env)
    created = client.post("/v1/seller/offers", json=OFFER)
    assert created.status_code == 200, created.text
    oid = created.json()["id"]
    assert client.get("/v1/seller/offers").json()[0]["price_cents"] == 29700
    for bad in (
        {**OFFER, "payment_url": "http://inseguro.example"},
        {**OFFER, "payment_url": "javascript:alert(1)"},
        {**OFFER, "payment_url": "https://"},
        {**OFFER, "name": "  "},
        {**OFFER, "price_cents": -1},
    ):
        res = client.post("/v1/seller/offers", json=bad)
        assert res.status_code in (400, 422), bad
    upd = client.put(
        f"/v1/seller/offers/{oid}", json={**OFFER, "price_cents": 100, "active": False}
    )
    assert upd.json()["price_cents"] == 100 and upd.json()["active"] is False
    assert client.put(f"/v1/seller/offers/{uuid.uuid4()}", json=OFFER).status_code == 404
    assert client.delete(f"/v1/seller/offers/{oid}").status_code == 200
    assert client.get("/v1/seller/offers").json() == []
    actions = env.sql("SELECT action FROM audit_log WHERE tenant_id = %s", (tid,))
    assert {"offer.created", "offer.updated", "offer.deleted"} <= {a[0] for a in actions}


def test_ai_settings_default_off(client: TestClient, env: Env) -> None:
    _owner(client, env)
    s = client.get("/v1/seller/settings").json()
    assert s["ai_enabled"] is False and s["active_offers"] == 0
    client.post("/v1/seller/offers", json=OFFER)
    on = client.put("/v1/seller/settings", json={"ai_enabled": True, "ai_persona": "Direto"})
    assert on.json()["ai_enabled"] is True and on.json()["active_offers"] == 1
    assert (
        client.put(
            "/v1/seller/settings", json={"ai_enabled": True, "ai_persona": "x" * 601}
        ).status_code
        == 422
    )


def test_inbox_reply_window_and_status(client: TestClient, env: Env, db: Database) -> None:
    tid = _owner(client, env)
    tenant = uuid.UUID(tid)
    with db.tx(tenant_id=tenant) as conn:
        _, conv = upsert_conversation(conn, tenant, "5511933332222", "Edu")
    cid = str(conv)
    # sem mensagem do cliente: janela fechada, só template
    assert (
        client.post(f"/v1/inbox/{cid}/reply", json={"body": "oi"}).json()["error"]["code"]
        == "window_closed"
    )
    env.sql("UPDATE conversations SET last_inbound_at = %s WHERE id = %s", (datetime.now(UTC), cid))
    env.sql(
        "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status) "
        "VALUES (%s, %s, 'in', 'customer', 'Olá', 'received')",
        (tid, cid),
    )
    row = client.get("/v1/inbox").json()[0]
    assert row["window_open"] is True and row["preview"] == "Olá" and "9333" not in row["phone"][:6]
    assert client.post(f"/v1/inbox/{cid}/reply", json={"body": "  "}).status_code == 400
    assert client.post(f"/v1/inbox/{cid}/reply", json={"body": "Posso ajudar!"}).json() == {
        "status": "queued"
    }
    detail = client.get(f"/v1/inbox/{cid}").json()
    assert detail["status"] == "human"
    assert [m["author"] for m in detail["messages"]] == ["customer", "human"]
    assert client.post(f"/v1/inbox/{cid}/status", json={"status": "bot"}).json() == {
        "status": "bot"
    }
    assert client.post(f"/v1/inbox/{cid}/status", json={"status": "x"}).status_code == 400
    assert client.get(f"/v1/inbox/{uuid.uuid4()}").status_code == 404
    assert client.get("/v1/inbox?status=nope").status_code == 400


def test_agent_reads_and_replies_but_cannot_change_offers(client: TestClient, env: Env) -> None:
    tid = _owner(client, env)
    agent = unique_email("agent")
    env.invite(tid, agent, "agent")
    client.post("/v1/auth/logout")
    assert login(client, agent).status_code == 200
    assert client.get("/v1/seller/offers").status_code == 200
    assert client.post("/v1/seller/offers", json=OFFER).status_code == 403
    assert client.put("/v1/seller/settings", json={"ai_enabled": True}).status_code == 403


def test_isolation_between_tenants_and_login_required(
    client: TestClient, env: Env, db: Database
) -> None:
    tid = _owner(client, env)
    client.post("/v1/seller/offers", json=OFFER)
    other = uuid.UUID(env.tenant("Outra loja", unique_email("o")))
    with db.tx(tenant_id=other) as conn:
        assert conn.execute("SELECT 1 FROM offers").fetchall() == []
    assert tid
    client.cookies.clear()
    assert client.get("/v1/inbox").status_code == 401
