"""Long-term recovery: review queue, never unsafe outbound, tenant-safe actions."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from fm_seller.db import Database
from fm_seller.events import normalize as n
from fm_seller.recovery.engine import handle_event
from tests.conftest import Env, login, unique_email
from tests.test_recovery_engine import NOW, Ctx, ev

PHONE = "5511999998888"


@pytest.fixture
def followup_ctx(env: Env, db: Database, client: TestClient) -> Ctx:
    # Existing case fixtures are mutually isolated; deletion cascades into tasks.
    env.sql("DELETE FROM recovery_steps")
    env.sql("DELETE FROM recovery_cases")
    email = unique_email("long")
    tid = env.tenant("Acompanhamento Longo", email)
    assert login(client, email).status_code == 200
    assert (
        client.put(
            "/v1/connections/whatsapp_cloud",
            json={
                "values": {
                    "phone_number_id": "1",
                    "waba_id": "2",
                    "access_token": "EAAGtoken123456",
                }
            },
        ).status_code
        == 200
    )
    env.sql("UPDATE connections SET status='connected' WHERE tenant_id=%s", (tid,))
    env.sql(
        "INSERT INTO tenant_settings(tenant_id, recovery_enabled, consent_declared_at) "
        "VALUES(%s,true,now())",
        (tid,),
    )
    return Ctx(env, db, tid)


def _make_evolution(ctx: Ctx) -> None:
    # The expires-before-decrypt branch does not need operational credentials.
    # This is strictly a DB/worker policy test; no external sending is performed.
    ctx.env.sql(
        "UPDATE connections SET provider='evolution' WHERE tenant_id=%s "
        "AND provider='whatsapp_cloud'",
        (str(ctx.tid),),
    )


def test_expired_evolution_cadence_creates_review_tasks_only(
    followup_ctx: Ctx, client: TestClient
) -> None:
    c = followup_ctx
    _make_evolution(c)
    c.handle(ev(ref="long-expired"))
    stats = c.run(NOW + timedelta(hours=30))
    assert stats.sent == 0 and stats.skipped == 2
    assert c.sender.sent == []
    items = client.get("/v1/recovery/followups").json()
    mine = [x for x in items if x["case_id"] == str(c.case("long-expired")["id"])]
    assert len(mine) == 2
    assert all(x["reason"] == "evolution_outside_window" for x in mine)
    assert all(x["status"] == "pending" for x in mine)
    assert all(x["contact"]["phone"].endswith("8888") for x in mine)
    assert PHONE not in str(mine)
    assert client.get("/v1/recovery/summary").json()["manual_followups_pending"] == 2

    # Reprocessing is idempotent; skipped steps are never picked again.
    assert c.run(NOW + timedelta(hours=30)).claimed == 0
    assert len(client.get("/v1/recovery/followups").json()) == 2


def test_review_action_is_not_a_message_and_is_audited(
    followup_ctx: Ctx, client: TestClient
) -> None:
    c = followup_ctx
    _make_evolution(c)
    c.handle(ev(ref="long-review"))
    c.run(NOW + timedelta(minutes=31))
    task = client.get("/v1/recovery/followups").json()[0]
    before = c.env.sql("SELECT count(*) FROM messages")[0][0]
    done = client.post(f"/v1/recovery/followups/{task['id']}/resolve", json={"action": "reviewed"})
    assert done.status_code == 200
    assert done.json()["status"] == "reviewed"
    assert c.env.sql("SELECT count(*) FROM messages")[0][0] == before
    assert client.get("/v1/recovery/followups").json() == []
    assert len(client.get("/v1/recovery/followups?status=reviewed").json()) == 1
    assert (
        client.post(
            f"/v1/recovery/followups/{task['id']}/resolve", json={"action": "reviewed"}
        ).status_code
        == 409
    )
    assert c.env.sql(
        "SELECT count(*) FROM audit_log WHERE tenant_id=%s AND action='recovery.followup_review'",
        (str(c.tid),),
    ) == [(1,)]


def test_purchase_and_opt_out_cancel_pending_review(followup_ctx: Ctx, client: TestClient) -> None:
    c = followup_ctx
    _make_evolution(c)
    c.handle(ev(ref="long-purchase"))
    c.run(NOW + timedelta(minutes=31))
    followup_id = client.get("/v1/recovery/followups").json()[0]["id"]
    with c.db.tx(tenant_id=c.tid) as conn:
        handle_event(
            conn, c.tid, ev(kind=n.PURCHASE_APPROVED, ref="paid-ref"), now=NOW + timedelta(hours=2)
        )
    assert client.get("/v1/recovery/followups").json() == []
    assert c.env.sql("SELECT status FROM recovery_followups WHERE id=%s", (followup_id,)) == [
        ("canceled",)
    ]
    assert (
        client.post(
            f"/v1/recovery/followups/{followup_id}/resolve", json={"action": "reviewed"}
        ).status_code
        == 409
    )

    c.handle(ev(ref="long-optout", product="Outro produto", phone="5511988887777", email=None))
    c.run(NOW + timedelta(minutes=63))
    tasks = client.get("/v1/recovery/followups").json()
    assert len(tasks) == 1
    stop = client.post("/v1/recovery/suppressions", json={"identity": "5511988887777"})
    assert stop.status_code == 200
    assert client.get("/v1/recovery/followups").json() == []
    assert c.env.sql("SELECT status FROM recovery_followups WHERE id=%s", (tasks[0]["id"],)) == [
        ("canceled",)
    ]


def test_cloud_missing_template_becomes_task_not_unsolicited_free_text(
    followup_ctx: Ctx, client: TestClient
) -> None:
    c = followup_ctx
    c.handle(ev(ref="long-cloud"))
    c.run(NOW + timedelta(minutes=35))
    assert c.sender.sent == []
    tasks = client.get("/v1/recovery/followups").json()
    assert len(tasks) == 1 and tasks[0]["reason"] == "cloud_template_unavailable"
    assert tasks[0]["template_key"] == "carrinho_1"
    assert (
        client.post(
            f"/v1/recovery/followups/{tasks[0]['id']}/resolve",
            json={"action": "dismissed"},
        ).status_code
        == 200
    )


def test_tenant_isolation_and_role_guard(followup_ctx: Ctx, client: TestClient, env: Env) -> None:
    c = followup_ctx
    _make_evolution(c)
    c.handle(ev(ref="long-isolation"))
    c.run(NOW + timedelta(minutes=35))
    id_ = client.get("/v1/recovery/followups").json()[0]["id"]
    other = unique_email("long-other")
    env.tenant("Outra loja", other)
    assert login(client, other).status_code == 200
    assert client.get("/v1/recovery/followups").json() == []
    assert (
        client.post(
            f"/v1/recovery/followups/{id_}/resolve", json={"action": "reviewed"}
        ).status_code
        == 404
    )
    assert c.env.sql("SELECT status FROM recovery_followups WHERE id=%s", (id_,)) == [("pending",)]


def test_invalid_resolutions_and_disabled_recovery_never_create_sends(
    followup_ctx: Ctx, client: TestClient
) -> None:
    c = followup_ctx
    _make_evolution(c)
    c.handle(ev(ref="long-invalid"))
    c.run(NOW + timedelta(minutes=35))
    id_ = client.get("/v1/recovery/followups").json()[0]["id"]
    assert (
        client.post(f"/v1/recovery/followups/{id_}/resolve", json={"action": "sent"}).status_code
        == 400
    )
    assert client.get("/v1/recovery/followups?status=bogus").status_code == 400
    c.env.sql(
        "UPDATE tenant_settings SET recovery_enabled=false WHERE tenant_id=%s",
        (str(c.tid),),
    )
    c.handle(ev(ref="should-not-open", product="Novo produto"))
    assert c.case("long-invalid")["status"] in ("open", "exhausted")
    assert c.sender.sent == []
