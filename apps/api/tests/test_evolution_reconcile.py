"""Database-backed confirmation with retry backoff and no resend."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from pydantic import SecretStr
from pytest import MonkeyPatch

from fm_seller.channels.evolution_reconcile import reconcile_uncertain_sends
from fm_seller.channels.whatsapp import upsert_conversation
from fm_seller.config import Settings
from fm_seller.db import Database
from tests.conftest import Env
from tests.test_evolution_runtime import _provision

PHONE = "5511999991234"
BODY = "Você recebeu o link para contratar."


def test_uncertain_evolution_send_reconciles_only_one_provider_record(
    client: TestClient, env: Env, db: Database, monkeypatch: MonkeyPatch
) -> None:
    tenant, instance, _ = _provision(client, env)
    tid = uuid.UUID(tenant)
    now = datetime.now(UTC)
    with db.tx(tenant_id=tid) as conn:
        _, conv_id = upsert_conversation(conn, tid, PHONE, "Cliente autorizado")
        inserted = conn.execute(
            "INSERT INTO messages "
            "(tenant_id,conversation_id,direction,author,body,status,claimed_at,created_at) "
            "VALUES (%s,%s,'out','bot',%s,'uncertain',%s,%s) RETURNING id",
            (tid, conv_id, BODY, now, now),
        ).fetchone()
        assert inserted is not None
        message_id = inserted["id"]

    class FakeLookup:
        def _call(self, method: str, route: str, **kwargs: Any) -> dict[str, Any]:
            assert method == "POST" and route == f"/chat/findMessages/{instance}"
            return {
                "messages": {
                    "records": [
                        {
                            "key": {
                                "remoteJid": f"{PHONE}@s.whatsapp.net",
                                "id": "EVOLUTION_PROOF_1",
                                "fromMe": True,
                            },
                            "message": {"conversation": BODY},
                            "messageTimestamp": int(now.timestamp()),
                        }
                    ]
                }
            }

    monkeypatch.setattr(
        "fm_seller.channels.evolution_reconcile.EvolutionClient",
        lambda **kwargs: FakeLookup(),
    )
    settings = Settings.model_construct(
        evolution_enabled=True,
        evolution_send_enabled=True,
        evolution_outbound_paused=False,
        evolution_api_base_url="https://evolution.test",
        evolution_api_key=SecretStr("a" * 48),
        evolution_timeout_seconds=3,
    )
    assert reconcile_uncertain_sends(db, settings, now=now) == 1
    with db.tx(tenant_id=tid) as conn:
        actual = conn.execute(
            "SELECT status, provider_message_id, evolution_reconcile_attempts "
            "FROM messages WHERE id=%s",
            (message_id,),
        ).fetchone()
    assert actual == {
        "status": "sent",
        "provider_message_id": "EVOLUTION_PROOF_1",
        "evolution_reconcile_attempts": 1,
    }
    # A second worker pass must never query or send the same message again.
    assert reconcile_uncertain_sends(db, settings, now=now) == 0
