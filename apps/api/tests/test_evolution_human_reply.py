"""Regression: Evolution manual replies vs Meta's 24-hour session rule."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi.testclient import TestClient

from fm_seller.channels.evolution_runtime import EvolutionSender, RoutedWhatsAppSender
from fm_seller.channels.outbox import flush_outbox
from fm_seller.channels.reply_policy import human_reply_allowed
from fm_seller.channels.whatsapp import upsert_conversation
from fm_seller.db import Database
from fm_seller.recovery.senders import SimulatedSender
from tests.conftest import Env, connect_whatsapp
from tests.test_evolution_runtime import FakeSendClient, _provision

PHONE = "5511999991234"


def _conversation(db: Database, env: Env, tenant: str) -> str:
    with db.tx(tenant_id=uuid.UUID(tenant)) as conn:
        _, conversation = upsert_conversation(conn, uuid.UUID(tenant), PHONE, "Cliente")
    env.sql(
        "UPDATE conversations SET last_inbound_at=now()-interval '30 hours', "
        "status='human' WHERE id=%s",
        (conversation,),
    )
    env.sql(
        "INSERT INTO messages(tenant_id,conversation_id,direction,author,body,status) "
        "VALUES(%s,%s,'in','customer','Olá, gostaria de ajuda','received')",
        (tenant, conversation),
    )
    return str(conversation)


def _sender() -> tuple[RoutedWhatsAppSender, FakeSendClient]:
    fake = FakeSendClient()
    return (
        RoutedWhatsAppSender(
            SimulatedSender(),
            EvolutionSender(cast(Any, fake)),
            meta_enabled=False,
            evolution_enabled=True,
            general_enabled=True,
        ),
        fake,
    )


def test_policy_allows_manual_evolution_but_never_cloud_or_uninitiated() -> None:
    now = datetime.now(UTC)
    old = now - timedelta(hours=30)
    assert human_reply_allowed("whatsapp", old, "evolution", now)
    assert not human_reply_allowed("whatsapp", old, "whatsapp_cloud", now)
    assert not human_reply_allowed("instagram", old, "evolution", now)
    assert not human_reply_allowed("whatsapp", None, "evolution", now)
    assert human_reply_allowed("whatsapp", now, None, now)


def test_evolution_human_late_reply_is_queued_once_sent_and_visible(
    client: TestClient, env: Env, db: Database
) -> None:
    tenant, _, _ = _provision(client, env)
    conv = _conversation(db, env, tenant)
    detail = client.get(f"/v1/inbox/{conv}")
    assert detail.status_code == 200
    data = detail.json()
    assert data["window_open"] is False
    assert data["reply_allowed"] is True
    assert data["evolution_manual_reply"] is True
    assert data["ai_enabled"] is False
    listed = client.get("/v1/inbox").json()
    assert next(x for x in listed if x["id"] == conv)["reply_allowed"] is True

    first = client.post(f"/v1/inbox/{conv}/reply", json={"body": "Posso ajudar."})
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "queued"

    route, fake = _sender()
    result = flush_outbox(db, env.box, route)
    assert result.sent == 1  # Shared test DB may have queued messages from other tenants.
    assert fake.sent == [{"number": PHONE, "text": "Posso ajudar."}]
    assert flush_outbox(db, env.box, route).sent == 0
    assert env.sql(
        "SELECT status, provider_message_id FROM messages "
        "WHERE conversation_id=%s AND author='human'",
        (conv,),
    ) == [("sent", "ABC123")]


def test_evolution_does_not_unlock_automatic_bot_or_recovery_after_24_hours(
    client: TestClient, env: Env, db: Database
) -> None:
    tenant, _, _ = _provision(client, env)
    conv = _conversation(db, env, tenant)
    env.sql(
        "INSERT INTO messages(tenant_id,conversation_id,direction,author,body,status) "
        "VALUES (%s,%s,'out','bot','Automático antigo','queued'), "
        "(%s,%s,'out','recovery','Recuperação antiga','queued')",
        (tenant, conv, tenant, conv),
    )
    route, fake = _sender()
    stats = flush_outbox(db, env.box, route)
    assert stats.failed == 2 and stats.sent == 0
    assert fake.sent == []
    assert env.sql(
        "SELECT status,error FROM messages WHERE conversation_id=%s "
        "AND author IN ('bot','recovery') ORDER BY author",
        (conv,),
    ) == [
        ("failed", "janela_24h_fechada"),
        ("failed", "janela_24h_fechada"),
    ]


def test_meta_cloud_and_dual_provider_keep_24h_guard(
    client: TestClient, env: Env, db: Database
) -> None:
    tenant, _, _ = _provision(client, env)
    conv = _conversation(db, env, tenant)
    # Ambiguous routing is prohibited; no arbitrary fallback to Evolution.
    connect_whatsapp(client, env, tenant)
    data = client.get(f"/v1/inbox/{conv}").json()
    assert data["reply_allowed"] is False
    blocked = client.post(f"/v1/inbox/{conv}/reply", json={"body": "oi"})
    assert blocked.status_code == 400
    assert blocked.json()["error"]["code"] == "window_closed"
    # With only Cloud connected, 24h rule still applies.
    env.sql(
        "UPDATE connections SET status='disconnected' WHERE tenant_id=%s AND provider='evolution'",
        (tenant,),
    )
    assert client.get(f"/v1/inbox/{conv}").json()["reply_allowed"] is False
    assert client.post(f"/v1/inbox/{conv}/reply", json={"body": "oi"}).status_code == 400


def test_evolution_no_inbound_no_manual_send_and_opt_out(
    client: TestClient, env: Env, db: Database
) -> None:
    tenant, _, _ = _provision(client, env)
    conv = _conversation(db, env, tenant)
    env.sql("UPDATE conversations SET last_inbound_at=NULL WHERE id=%s", (conv,))
    assert client.get(f"/v1/inbox/{conv}").json()["reply_allowed"] is False
    assert client.post(f"/v1/inbox/{conv}/reply", json={"body": "oi"}).status_code == 400
    env.sql(
        "UPDATE conversations SET last_inbound_at=now()-interval '30 hours' WHERE id=%s",
        (conv,),
    )
    env.sql(
        "INSERT INTO suppressions(tenant_id,identity,reason) VALUES(%s,%s,%s)",
        (tenant, PHONE, "opt_out"),
    )
    assert client.get(f"/v1/inbox/{conv}").json()["reply_allowed"] is False
    blocked = client.post(f"/v1/inbox/{conv}/reply", json={"body": "oi"})
    assert blocked.status_code == 403
    assert env.sql(
        "SELECT count(*) FROM messages WHERE conversation_id=%s AND author='human'",
        (conv,),
    ) == [(0,)]


def test_disabled_ai_cannot_be_reenabled_by_conversation_status(
    client: TestClient, env: Env, db: Database
) -> None:
    tenant, _, _ = _provision(client, env)
    conv = _conversation(db, env, tenant)
    res = client.post(f"/v1/inbox/{conv}/status", json={"status": "bot"})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "seller_ai_disabled"
    assert env.sql("SELECT status FROM conversations WHERE id=%s", (conv,)) == [("human",)]
    # The business owner may activate AI via the standard onboarding/settings.
    # Here we emulate that already-approved state change without modifying production.
    env.sql(
        "INSERT INTO tenant_settings(tenant_id,ai_enabled,ai_persona) "
        "VALUES(%s,true,'') ON CONFLICT(tenant_id) "
        "DO UPDATE SET ai_enabled=true",
        (tenant,),
    )
    assert client.post(f"/v1/inbox/{conv}/status", json={"status": "bot"}).status_code == 200
