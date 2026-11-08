"""Verified payment-link intent creates a bounded, neutral and idempotent follow-up."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi.testclient import TestClient

from fm_seller.db import Database
from fm_seller.recovery.checkout_link import (
    FIRST_FOLLOWUP,
    record_confirmed_checkout_link,
    scan_confirmed_checkout_links,
)
from tests.conftest import Env
from tests.test_opportunities import _cold, _seed
from tests.test_whatsapp_inbound import PHONE as WA_PHONE


def _link(
    env: Env, tid: str, phone: str = WA_PHONE, *, status: str = "sent"
) -> tuple[uuid.UUID, datetime]:
    conv_id = _seed(env, tid, phone=phone, hours_ago=1)
    row = env.sql(
        "SELECT id, created_at FROM messages WHERE conversation_id = %s AND direction = 'out'",
        (conv_id,),
    )[0]
    env.sql(
        "UPDATE messages SET body = %s, status = %s, provider_message_id = %s WHERE id = %s",
        (
            "Perfeito! Plano Essencial, para contratar faça o pagamento neste link seguro:\n"
            "https://pay.example.test/plano-essencial",
            status,
            "evo-ack-" + str(row[0]) if status == "sent" else None,
            row[0],
        ),
    )
    return uuid.UUID(str(row[0])), row[1]


def _offer(env: Env, tid: str) -> None:
    env.sql(
        "INSERT INTO offers (tenant_id,name,description,price_cents,payment_url) "
        "VALUES (%s,'Plano Essencial','Assinatura mensal',12990,%s)",
        (tid, "https://pay.example.test/plano-essencial"),
    )


def test_verified_checkout_link_creates_neutral_followup_45_minutes_after_delivery(
    client: TestClient, env: Env, db: Database
) -> None:
    tid = _cold(client, env)
    _offer(env, tid)
    message_id, created = _link(env, tid)
    with db.tx(tenant_id=uuid.UUID(tid)) as conn:
        assert record_confirmed_checkout_link(conn, uuid.UUID(tid), message_id)
    case = env.sql(
        "SELECT source, trigger_kind, product_name, amount_cents, external_ref "
        "FROM recovery_cases WHERE tenant_id=%s",
        (tid,),
    )
    assert case == [
        ("conversa", "conversation_cold", "Plano Essencial", 12990, f"offer-link:{message_id}")
    ]
    step = env.sql(
        "SELECT step_no, template_key, first_scheduled_at FROM recovery_steps "
        "WHERE tenant_id=%s AND step_no=1",
        (tid,),
    )[0]
    assert step[0] == 1 and step[1] == "conversa_1"
    assert step[2] - created == FIRST_FOLLOWUP
    with db.tx(tenant_id=uuid.UUID(tid)) as conn:
        assert not record_confirmed_checkout_link(conn, uuid.UUID(tid), message_id)
    assert env.sql("SELECT count(*) FROM recovery_cases WHERE tenant_id=%s", (tid,)) == [(1,)]


def test_unconfirmed_or_answered_link_does_not_claim_unpaid_checkout(
    client: TestClient, env: Env, db: Database
) -> None:
    tid = _cold(client, env)
    _offer(env, tid)
    message_id, _ = _link(env, tid, status="uncertain")
    with db.tx(tenant_id=uuid.UUID(tid)) as conn:
        assert not record_confirmed_checkout_link(conn, uuid.UUID(tid), message_id)
    assert scan_confirmed_checkout_links(db) == 0
    env.sql(
        "UPDATE messages SET status='sent', provider_message_id='evo-ack' WHERE id=%s",
        (message_id,),
    )
    env.sql(
        "UPDATE conversations SET last_inbound_at = now() "
        "WHERE id=(SELECT conversation_id FROM messages WHERE id=%s)",
        (message_id,),
    )
    with db.tx(tenant_id=uuid.UUID(tid)) as conn:
        assert not record_confirmed_checkout_link(conn, uuid.UUID(tid), message_id)


def test_pilot_scanner_only_records_authorized_contact(
    client: TestClient, env: Env, db: Database
) -> None:
    tid = _cold(client, env)
    _offer(env, tid)
    allowed_id, _ = _link(env, tid, WA_PHONE)
    other_id, _ = _link(env, tid, "5511988887777")
    assert allowed_id != other_id
    scope = (uuid.UUID(tid), WA_PHONE)
    assert scan_confirmed_checkout_links(db, pilot_scope=scope) == 1
    assert scan_confirmed_checkout_links(db, pilot_scope=scope) == 0
    assert env.sql("SELECT external_ref FROM recovery_cases WHERE tenant_id=%s", (tid,)) == [
        (f"offer-link:{allowed_id}",)
    ]
