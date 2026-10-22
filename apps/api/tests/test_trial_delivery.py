"""Cross-pipeline proof: trial caps actual outbox sends and blocks AI after expiry."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from fm_seller.ai.model import SimulatedAiModel
from fm_seller.ai.seller import run_ai_replies
from fm_seller.channels.outbox import flush_outbox
from fm_seller.channels.whatsapp import upsert_conversation
from fm_seller.db import Database
from fm_seller.provisioning import trial
from fm_seller.recovery.senders import SimulatedSender
from tests.conftest import Env, connect_whatsapp, login, unique_email

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def _tenant(env: Env, db: Database, client: TestClient) -> uuid.UUID:
    email = unique_email("trial-send")
    tenant_id = uuid.UUID(env.tenant("Trial integrado", email))
    assert login(client, email).status_code == 200
    actor = env.sql("SELECT user_id FROM memberships WHERE tenant_id = %s", (tenant_id,))[0][0]
    assert trial.start(db, tenant_id, actor=actor, now=NOW)["status"] == "active"
    return tenant_id


def test_outbox_sends_only_last_trial_message_across_two_claimed(
    env: Env, db: Database, client: TestClient
) -> None:
    tenant_id = _tenant(env, db, client)
    connect_whatsapp(client, env, str(tenant_id))
    env.sql(
        "UPDATE tenant_plans SET trial_messages_sent = 499 WHERE tenant_id = %s",
        (tenant_id,),
    )
    with db.tx(tenant_id=tenant_id) as conn:
        _, conv = upsert_conversation(conn, tenant_id, "5511988811111", "Piloto")
    env.sql("UPDATE conversations SET last_inbound_at = %s WHERE id = %s", (NOW, conv))
    ids = []
    for item in (1, 2):
        rows = env.sql(
            "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status) "
            "VALUES (%s, %s, 'out', 'bot', %s, 'queued') RETURNING id",
            (tenant_id, conv, f"Mensagem {item}"),
        )
        ids.append(rows[0][0])
    sender = SimulatedSender()
    result = flush_outbox(db, env.box, sender, now=NOW, limit=2)
    assert (result.sent, result.failed) == (1, 1)
    assert len(sender.sent) == 1
    states = env.sql(
        "SELECT status FROM messages WHERE id = ANY(%s) ORDER BY created_at",
        (ids,),
    )
    assert sorted(x[0] for x in states) == ["failed", "sent"]
    with db.tx(tenant_id=tenant_id) as conn:
        state = trial.snapshot(conn, tenant_id, now=NOW)
        assert state is not None and state["used"] == 500
        assert state["remaining"] == 0


def test_expired_trial_prevents_new_model_call(env: Env, db: Database, client: TestClient) -> None:
    tenant_id = _tenant(env, db, client)
    env.sql("UPDATE messages SET handled = true WHERE direction = 'in'")
    with db.tx(tenant_id=tenant_id) as conn:
        _, conv = upsert_conversation(conn, tenant_id, "5511988822222", "Piloto")
    env.sql(
        "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status, "
        "handled) VALUES (%s, %s, 'in', 'customer', 'Oi', 'received', false)",
        (tenant_id, conv),
    )
    model = SimulatedAiModel()
    result = run_ai_replies(db, model, now=NOW + timedelta(days=7))
    assert result.handoffs >= 1
    assert model.calls == []
    assert env.sql("SELECT status, handoff_reason FROM conversations WHERE id = %s", (conv,)) == [
        ("human", "trial_limit_or_expired")
    ]
