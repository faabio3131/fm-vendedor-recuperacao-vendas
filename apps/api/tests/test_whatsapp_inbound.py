"""Entrada do WhatsApp. Payloads seguem a documentação da Meta, mas sintéticos (sem conta real)."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fm_seller.channels.outbox import flush_outbox
from fm_seller.db import Database
from fm_seller.recovery.optout import is_opt_out
from fm_seller.recovery.senders import SimulatedSender, UnavailableSender
from tests.conftest import Env, login, unique_email

APP_SECRET = "app-secret-de-teste"
NUMBER = "1055550001"
PHONE = "5511955554444"


def sign(raw: bytes, secret: str = APP_SECRET) -> dict[str, str]:
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={digest}", "Content-Type": "application/json"}


def payload(
    messages: list[dict[str, Any]] | None = None,
    statuses: list[dict[str, Any]] | None = None,
    number: str = NUMBER,
) -> bytes:
    value: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "metadata": {"phone_number_id": number},
        "contacts": [{"wa_id": PHONE, "profile": {"name": "Carla"}}],
    }
    if messages:
        value["messages"] = messages
    if statuses:
        value["statuses"] = statuses
    body = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": value}]}]}
    return json.dumps(body).encode()


def text(mid: str, body: str) -> dict[str, Any]:
    return {"from": PHONE, "id": mid, "type": "text", "text": {"body": body}}


@pytest.fixture
def wa(client: TestClient, env: Env) -> tuple[TestClient, str, str, str]:
    # A fila de saída é global: descarta o que outros testes deixaram para medir só este cenário.
    env.sql("UPDATE messages SET status = 'failed' WHERE direction = 'out' AND status = 'queued'")
    email = unique_email("wa")
    tid = env.tenant("Loja WA", email)
    assert login(client, email).status_code == 200
    res = client.put(
        "/v1/connections/whatsapp_cloud",
        json={
            "values": {
                "phone_number_id": NUMBER,
                "waba_id": "2",
                "access_token": "EAAGtoken123456",
                "app_secret": APP_SECRET,
            }
        },
    )
    assert res.status_code == 200, res.text
    pid = res.json()["webhook_url"].rsplit("/", 1)[1]
    return client, tid, pid, res.json()["webhook_secret_once"]


def post(c: TestClient, pid: str, raw: bytes, headers: dict[str, str] | None = None) -> Any:
    return c.post(f"/v1/webhooks/whatsapp_cloud/{pid}", content=raw, headers=headers or sign(raw))


def test_handshake_requires_the_generated_token(wa: tuple[TestClient, str, str, str]) -> None:
    c, _, pid, token = wa
    q = f"/v1/webhooks/whatsapp_cloud/{pid}"
    ok = c.get(
        q, params={"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "42"}
    )
    assert ok.status_code == 200 and ok.text == "42"
    bad = c.get(q, params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "42"})
    assert bad.status_code == 403
    assert c.get(q, params={"hub.verify_token": token, "hub.challenge": "1"}).status_code == 403


def test_signature_is_mandatory(wa: tuple[TestClient, str, str, str], env: Env) -> None:
    c, tid, pid, _ = wa
    raw = payload([text("wamid.1", "oi")])
    assert post(c, pid, raw, {"Content-Type": "application/json"}).status_code == 401
    assert post(c, pid, raw, sign(raw, "outro-segredo")).status_code == 401
    assert env.sql("SELECT count(*) FROM messages WHERE tenant_id = %s", (tid,))[0][0] == 0


def test_missing_app_secret_rejects_everything(client: TestClient, env: Env) -> None:
    email = unique_email("wa2")
    env.tenant("Loja WA2", email)
    assert login(client, email).status_code == 200
    res = client.put(
        "/v1/connections/whatsapp_cloud",
        json={"values": {"phone_number_id": NUMBER, "waba_id": "2", "access_token": "EAAGtoken1"}},
    )
    pid = res.json()["webhook_url"].rsplit("/", 1)[1]
    raw = payload([text("wamid.x", "oi")])
    out = post(client, pid, raw)
    assert out.status_code == 401 and out.json()["error"]["code"] == "app_secret_missing"


def test_inbound_message_creates_contact_conversation_and_dedupes(
    wa: tuple[TestClient, str, str, str], env: Env
) -> None:
    c, tid, pid, _ = wa
    raw = payload([text("wamid.10", "Oi, quero saber mais")])
    first = post(c, pid, raw)
    assert first.status_code == 200 and first.json()["messages"] == 1
    again = post(c, pid, raw)
    assert again.json()["messages"] == 0 and again.json()["duplicates"] == 1
    rows = env.sql(
        "SELECT m.body, m.handled, cv.status, ct.name FROM messages m "
        "JOIN conversations cv ON cv.id = m.conversation_id "
        "JOIN contacts ct ON ct.id = cv.contact_id "
        "WHERE m.tenant_id = %s",
        (tid,),
    )
    assert rows == [("Oi, quero saber mais", False, "bot", "Carla")]
    status = env.sql("SELECT status FROM connections WHERE tenant_id = %s", (tid,))
    assert status[0][0] == "connected"


def test_other_phone_number_events_are_ignored(
    wa: tuple[TestClient, str, str, str], env: Env
) -> None:
    c, tid, pid, _ = wa
    out = post(c, pid, payload([text("wamid.20", "oi")], number="999"))
    assert out.json()["messages"] == 0
    assert env.sql("SELECT count(*) FROM messages WHERE tenant_id = %s", (tid,))[0][0] == 0


@pytest.mark.parametrize(
    ("msg", "expected"),
    [
        ("SAIR", True),
        ("Por favor, parar de me mandar mensagem", True),
        ("não quero mais receber", True),
        ("Stop!", True),
        ("Quero comprar o curso", False),
        ("", False),
        ("a" * 200 + " sair", False),
    ],
)
def test_opt_out_detection(msg: str, expected: bool) -> None:
    assert is_opt_out(msg) is expected


def test_opt_out_message_blocks_contact_stops_cases_and_queues_confirmation(
    wa: tuple[TestClient, str, str, str], env: Env, db: Database
) -> None:
    from fm_seller.events import normalize as n
    from fm_seller.events.normalize import CheckoutEvent
    from fm_seller.recovery.engine import handle_event

    c, tid, pid, _ = wa
    env.sql(
        "INSERT INTO tenant_settings (tenant_id, recovery_enabled, consent_declared_at) "
        "VALUES (%s, true, now())",
        (tid,),
    )
    ev = CheckoutEvent(n.PIX_PENDING, "x", "oo-1", "Carla", None, PHONE, "p", "Curso", 100, None)
    with db.tx(tenant_id=uuid.UUID(tid)) as conn:
        handle_event(conn, uuid.UUID(tid), ev, now=datetime.now(UTC))
    out = post(c, pid, payload([text("wamid.30", "SAIR")]))
    assert out.json()["opt_outs"] == 1
    assert env.sql("SELECT identity FROM suppressions WHERE tenant_id = %s", (tid,)) == [(PHONE,)]
    case = env.sql("SELECT status, closed_reason FROM recovery_cases WHERE tenant_id = %s", (tid,))
    assert case == [("stopped", "opt_out")]
    conv = env.sql("SELECT status FROM conversations WHERE tenant_id = %s", (tid,))
    assert conv == [("closed",)]
    queued = env.sql(
        "SELECT author, status FROM messages WHERE tenant_id = %s AND direction = 'out'", (tid,)
    )
    assert queued == [("system", "queued")]


def test_delivery_status_comes_from_provider_and_never_goes_backwards(
    wa: tuple[TestClient, str, str, str], env: Env
) -> None:
    c, tid, pid, _ = wa
    post(c, pid, payload([text("wamid.40", "oi")]))
    conv = env.sql("SELECT id FROM conversations WHERE tenant_id = %s", (tid,))[0][0]
    env.sql(
        "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status, "
        "provider_message_id) VALUES (%s, %s, 'out', 'bot', 'x', 'sent', 'wamid.out1')",
        (tid, conv),
    )

    def st(status: str, **extra: Any) -> bytes:
        return payload(statuses=[{"id": "wamid.out1", "status": status, **extra}])

    def current() -> tuple[str, str | None]:
        row = env.sql("SELECT status, error FROM messages WHERE provider_message_id = 'wamid.out1'")
        return row[0]

    post(c, pid, st("read"))
    assert current() == ("read", None)
    post(c, pid, st("delivered"))  # chegou atrasado: não rebaixa
    assert current()[0] == "read"
    post(c, pid, st("failed", errors=[{"title": "Número sem WhatsApp"}]))
    assert current() == ("failed", "Número sem WhatsApp")


def test_outbox_sends_text_inside_24h_window_only(
    wa: tuple[TestClient, str, str, str], env: Env, db: Database
) -> None:
    c, tid, pid, _ = wa
    post(c, pid, payload([text("wamid.50", "oi")]))
    conv = env.sql("SELECT id FROM conversations WHERE tenant_id = %s", (tid,))[0][0]
    for body in ("resposta dentro da janela",):
        env.sql(
            "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status) "
            "VALUES (%s, %s, 'out', 'bot', %s, 'queued')",
            (tid, conv, body),
        )
    sender = SimulatedSender()
    stats = flush_outbox(db, env.box, sender, now=datetime.now(UTC) + timedelta(hours=1))
    assert stats.sent == 1 and sender.texts == [(PHONE, "resposta dentro da janela")]
    assert env.sql("SELECT status FROM messages WHERE body = 'resposta dentro da janela'") == [
        ("sent",)
    ]
    env.sql(
        "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status) "
        "VALUES (%s, %s, 'out', 'bot', 'tarde demais', 'queued')",
        (tid, conv),
    )
    late = flush_outbox(db, env.box, sender, now=datetime.now(UTC) + timedelta(hours=30))
    assert late.failed == 1 and len(sender.texts) == 1
    row = env.sql("SELECT status, error FROM messages WHERE body = 'tarde demais'")
    assert row == [("failed", "janela_24h_fechada")]


def test_outbox_never_sends_with_unavailable_sender_or_twice(
    wa: tuple[TestClient, str, str, str], env: Env, db: Database
) -> None:
    c, tid, pid, _ = wa
    post(c, pid, payload([text("wamid.60", "oi")]))
    conv = env.sql("SELECT id FROM conversations WHERE tenant_id = %s", (tid,))[0][0]
    env.sql(
        "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status) "
        "VALUES (%s, %s, 'out', 'bot', 'uma vez só', 'queued')",
        (tid, conv),
    )
    assert flush_outbox(db, env.box, UnavailableSender()).claimed == 0
    sender = SimulatedSender()
    flush_outbox(db, env.box, sender)
    flush_outbox(db, env.box, sender)
    assert [t for _, t in sender.texts].count("uma vez só") == 1
