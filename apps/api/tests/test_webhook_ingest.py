"""Recebimento de webhooks. Payloads sintéticos: formato real a confirmar (ver normalize.py)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fm_seller.api.app import create_app
from fm_seller.db import Conn, Database
from fm_seller.events.ingest import reprocess_failed
from fm_seller.events.normalize import CheckoutEvent
from tests.conftest import ORIGIN, Env, login, unique_email

SECRET = "segredo-cakto-de-teste-123"


class Recorder:
    def __init__(self) -> None:
        self.events: list[CheckoutEvent] = []
        self.fail = False

    def __call__(self, conn: Conn, tenant_id: uuid.UUID, event: CheckoutEvent) -> None:
        if self.fail:
            raise RuntimeError("falha simulada")
        self.events.append(event)


@pytest.fixture
def rec() -> Recorder:
    return Recorder()


@pytest.fixture
def wclient(env: Env, db: Database, rec: Recorder) -> Iterator[TestClient]:
    app = create_app(env.settings(), db=db, box=env.box, event_handler=rec)
    with TestClient(app, headers={"Origin": ORIGIN}) as c:
        yield c


def _setup(client: TestClient, env: Env, provider: str = "cakto") -> tuple[str, str]:
    email = unique_email("wh")
    tenant_id = env.tenant("Loja Webhook", email)
    assert login(client, email).status_code == 200
    values = {"webhook_secret": SECRET} if provider == "cakto" else {"hottok": SECRET}
    res = client.put(f"/v1/connections/{provider}", json={"values": values})
    assert res.status_code == 200, res.text
    return tenant_id, res.json()["webhook_url"].rsplit("/", 1)[1]


def _cakto(ref: str, event: str = "checkout_abandonment") -> dict[str, Any]:
    return {
        "event": event,
        "secret": SECRET,
        "data": {
            "id": ref,
            "customer": {"name": "Ana", "email": "ana@example.test", "phone": "11999998888"},
            "product": {"id": "p1", "name": "Curso"},
            "amount": 97,
        },
    }


def test_valid_event_is_stored_encrypted_and_handled(
    wclient: TestClient, env: Env, rec: Recorder
) -> None:
    tenant_id, pid = _setup(wclient, env)
    res = wclient.post(f"/v1/webhooks/cakto/{pid}", json=_cakto("e-1"))
    assert res.status_code == 200 and res.json() == {"status": "accepted"}
    assert len(rec.events) == 1 and rec.events[0].external_ref == "e-1"
    row = env.sql(
        "SELECT status, payload_encrypted FROM webhook_events WHERE tenant_id = %s", (tenant_id,)
    )[0]
    assert row[0] == "processed"
    assert b"ana@example.test" not in bytes(row[1])
    status = env.sql("SELECT status FROM connections WHERE tenant_id = %s", (tenant_id,))[0][0]
    assert status == "connected"


def test_wrong_or_missing_secret_is_401_and_nothing_is_stored(
    wclient: TestClient, env: Env, rec: Recorder
) -> None:
    tenant_id, pid = _setup(wclient, env)
    bad = _cakto("e-2")
    bad["secret"] = "errado"
    assert wclient.post(f"/v1/webhooks/cakto/{pid}", json=bad).status_code == 401
    none = _cakto("e-3")
    del none["secret"]
    assert wclient.post(f"/v1/webhooks/cakto/{pid}", json=none).status_code == 401
    assert rec.events == []
    count = env.sql("SELECT count(*) FROM webhook_events WHERE tenant_id = %s", (tenant_id,))
    assert count[0][0] == 0


def test_secret_accepted_via_bearer_header(wclient: TestClient, env: Env) -> None:
    _, pid = _setup(wclient, env)
    body = _cakto("e-4")
    del body["secret"]
    res = wclient.post(
        f"/v1/webhooks/cakto/{pid}", json=body, headers={"Authorization": f"Bearer {SECRET}"}
    )
    assert res.status_code == 200


def test_hotmart_hottok_header(wclient: TestClient, env: Env, rec: Recorder) -> None:
    _, pid = _setup(wclient, env, "hotmart")
    body = {
        "id": "hm-1",
        "event": "PURCHASE_OUT_OF_SHOPPING_CART",
        "data": {"buyer": {"email": "bia@example.test"}, "product": {"id": 5}},
    }
    assert wclient.post(f"/v1/webhooks/hotmart/{pid}", json=body).status_code == 401
    res = wclient.post(
        f"/v1/webhooks/hotmart/{pid}", json=body, headers={"X-Hotmart-Hottok": SECRET}
    )
    assert res.status_code == 200 and len(rec.events) == 1


def test_unknown_public_id_and_provider(wclient: TestClient) -> None:
    assert wclient.post("/v1/webhooks/cakto/nao-existe", json={}).status_code == 404
    assert wclient.post("/v1/webhooks/whatsapp_cloud/x", json={}).status_code == 404


def test_public_id_of_other_provider_does_not_match(wclient: TestClient, env: Env) -> None:
    _, pid = _setup(wclient, env, "cakto")
    assert wclient.post(f"/v1/webhooks/hotmart/{pid}", json=_cakto("x")).status_code == 404


def test_invalid_json_and_oversize(wclient: TestClient, env: Env) -> None:
    _, pid = _setup(wclient, env)
    r = wclient.post(
        f"/v1/webhooks/cakto/{pid}",
        content=b"{nao json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    r = wclient.post(
        f"/v1/webhooks/cakto/{pid}", content=b"[1,2]", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 400
    big = b'{"x":"' + b"a" * (300 * 1024) + b'"}'
    r = wclient.post(
        f"/v1/webhooks/cakto/{pid}", content=big, headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 413


def test_duplicate_delivery_does_not_repeat_effect(
    wclient: TestClient, env: Env, rec: Recorder
) -> None:
    tenant_id, pid = _setup(wclient, env)
    body = _cakto("e-5") | {"id": "evt-dup"}
    assert wclient.post(f"/v1/webhooks/cakto/{pid}", json=body).json() == {"status": "accepted"}
    assert wclient.post(f"/v1/webhooks/cakto/{pid}", json=body).json() == {"status": "duplicate"}
    assert len(rec.events) == 1
    count = env.sql("SELECT count(*) FROM webhook_events WHERE tenant_id = %s", (tenant_id,))
    assert count[0][0] == 1


def test_unknown_event_is_ignored(wclient: TestClient, env: Env, rec: Recorder) -> None:
    tenant_id, pid = _setup(wclient, env)
    res = wclient.post(f"/v1/webhooks/cakto/{pid}", json=_cakto("e-6", "evento_novo"))
    assert res.status_code == 200 and res.json() == {"status": "ignored"}
    assert rec.events == []
    status = env.sql("SELECT status FROM webhook_events WHERE tenant_id = %s", (tenant_id,))
    assert status[0][0] == "ignored"


def test_handler_failure_keeps_event_answers_200_and_can_be_reprocessed(
    wclient: TestClient, env: Env, db: Database, rec: Recorder
) -> None:
    tenant_id, pid = _setup(wclient, env)
    rec.fail = True
    res = wclient.post(f"/v1/webhooks/cakto/{pid}", json=_cakto("e-7"))
    assert res.status_code == 200 and res.json() == {"status": "failed"}
    row = env.sql("SELECT status, error FROM webhook_events WHERE tenant_id = %s", (tenant_id,))[0]
    assert row[0] == "failed" and "falha simulada" in row[1]

    rec.fail = False
    assert reprocess_failed(db, env.box, rec) >= 1
    assert [e.external_ref for e in rec.events] == ["e-7"]
    status = env.sql("SELECT status FROM webhook_events WHERE tenant_id = %s", (tenant_id,))
    assert status[0][0] == "processed"


def test_suspended_tenant_is_refused(wclient: TestClient, env: Env, rec: Recorder) -> None:
    tenant_id, pid = _setup(wclient, env)
    env.sql("UPDATE tenants SET status = 'suspended' WHERE id = %s", (tenant_id,))
    assert wclient.post(f"/v1/webhooks/cakto/{pid}", json=_cakto("e-8")).status_code == 403
    assert rec.events == []


def test_webhook_events_are_isolated_between_tenants(
    wclient: TestClient, env: Env, db: Database
) -> None:
    t1, pid = _setup(wclient, env)
    wclient.post(f"/v1/webhooks/cakto/{pid}", json=_cakto("e-9"))
    other = env.tenant("Outra Loja", unique_email("o"))
    with db.tx(tenant_id=uuid.UUID(other)) as conn:
        rows = conn.execute("SELECT id FROM webhook_events").fetchall()
        assert rows == []
    with db.tx(tenant_id=uuid.UUID(t1)) as conn:
        assert len(conn.execute("SELECT id FROM webhook_events").fetchall()) >= 1
