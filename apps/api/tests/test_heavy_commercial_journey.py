"""Heavy commercial pipeline tests with real PostgreSQL and simulated Evolution.

No calls to Gemini, WhatsApp, checkout or a real customer. The goal is to
exercise the canonical API -> DB -> AI -> outbox -> provider-ack path.
"""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

from fastapi.testclient import TestClient

from fm_seller.ai.model import AiReply, SimulatedAiModel
from fm_seller.ai.seller import run_ai_replies
from fm_seller.channels.evolution_runtime import EvolutionSender, RoutedWhatsAppSender
from fm_seller.channels.outbox import flush_outbox
from fm_seller.db import Database
from fm_seller.recovery.senders import SimulatedSender
from tests.conftest import Env
from tests.test_evolution_runtime import FakeSendClient, _provision

PHONE = "5511999991234"
PAYMENT_LINK = "https://checkout.example.test/produto-real-da-fixture"


def _webhook(
    client: TestClient, instance: str, key: str, message_id: str, body: str
) -> dict[str, int]:
    payload = {
        "event": "messages.upsert",
        "instance": instance,
        "data": {
            "key": {
                "remoteJid": f"{PHONE}@s.whatsapp.net",
                "fromMe": False,
                "id": message_id,
            },
            "message": {"conversation": body},
            "pushName": "Cliente QA",
        },
    }
    result = client.post(
        f"/v1/webhooks/evolution/{instance}",
        content=json.dumps(payload).encode(),
        headers={"x-fm-evolution-auth": key},
    )
    assert result.status_code == 200, result.text
    return cast(dict[str, int], result.json())


def _setup_offer(env: Env, tenant: str) -> str:
    env.sql(
        "INSERT INTO tenant_settings (tenant_id,ai_enabled,ai_persona) "
        "VALUES (%s,true,'Atendimento profissional, sem improvisar preço')",
        (tenant,),
    )
    return str(
        env.sql(
            "INSERT INTO offers (tenant_id,name,description,price_cents,payment_url) "
            "VALUES (%s,'Produto QA','Prova comercial',19700,%s) RETURNING id",
            (tenant, PAYMENT_LINK),
        )[0][0]
    )


def test_webhook_to_registered_price_link_to_evolution_ack(
    env: Env, db: Database, client: TestClient
) -> None:
    """Real DB/API flow, authentic webhook, one outbound and monotonic read receipt."""
    env.sql("UPDATE messages SET handled=true WHERE direction='in'")
    # The integration suite shares a synthetic DB across tests. Its older outbox
    # rows must not be claimed as part of this new tenant's end-to-end case.
    env.sql(
        "UPDATE messages SET status='failed', error='qa_preexisting_queue' "
        "WHERE direction='out' AND status='queued'"
    )
    tenant, instance, key = _provision(client, env)
    offer_id = _setup_offer(env, tenant)
    inbound = _webhook(client, instance, key, "commercial-flow-1", "Quanto custa?")
    assert inbound["messages"] == 1
    assert _webhook(client, instance, key, "commercial-flow-1", "Quanto custa?")["duplicates"] == 1

    model = SimulatedAiModel(
        forced=AiReply("A {oferta} custa {preco}. Veja {link}", offer_id=offer_id)
    )
    result = run_ai_replies(db, model)
    assert result.replied == 1
    assert len(model.calls) == 1
    assert model.calls[0].offers[0].price_text == "R$ 197,00"

    fake = FakeSendClient()
    sender = RoutedWhatsAppSender(
        SimulatedSender(),
        EvolutionSender(cast(Any, fake)),
        meta_enabled=False,
        evolution_enabled=True,
    )
    sent = flush_outbox(db, env.box, sender)
    assert (sent.sent, sent.failed) == (1, 0)
    assert fake.sent == [
        {
            "number": PHONE,
            "text": "A Produto QA custa R$ 197,00. Veja " + PAYMENT_LINK,
        }
    ]
    assert flush_outbox(db, env.box, sender).sent == 0

    receipt = {
        "event": "messages.update",
        "instance": instance,
        "data": {"key": {"id": "ABC123"}, "status": "READ"},
    }
    ack = client.post(
        f"/v1/webhooks/evolution/{instance}",
        content=json.dumps(receipt).encode(),
        headers={"x-fm-evolution-auth": key},
    )
    assert ack.status_code == 200, ack.text
    assert env.sql(
        "SELECT status FROM messages WHERE tenant_id=%s "
        "AND direction='out' AND provider_message_id='ABC123'",
        (tenant,),
    ) == [("read",)]
    assert env.sql(
        "SELECT count(*) FROM messages WHERE tenant_id=%s AND direction='out'",
        (tenant,),
    ) == [(1,)]


def test_fraudulent_model_price_is_handed_to_human(
    env: Env, db: Database, client: TestClient
) -> None:
    """An LLM cannot inject a lower price, invented payment endpoint or Pix."""
    env.sql("UPDATE messages SET handled=true WHERE direction='in'")
    tenant, instance, key = _provision(client, env)
    _setup_offer(env, tenant)
    assert _webhook(client, instance, key, "commercial-fraud-1", "Tem desconto?")["messages"] == 1
    model = SimulatedAiModel(
        forced=AiReply("Só hoje R$ 0,01. Pague em https://fraude.example.test/pix")
    )
    assert run_ai_replies(db, model).handoffs == 1
    assert env.sql(
        "SELECT status,handoff_reason FROM conversations WHERE tenant_id=%s",
        (tenant,),
    ) == [("human", "resposta_invalida")]
    messages = env.sql(
        "SELECT body FROM messages WHERE tenant_id=%s AND direction='out'", (tenant,)
    )
    assert not any("fraude.example" in body or "0,01" in body for (body,) in messages)


def test_40_inbound_events_eight_concurrent_workers_generate_one_reply(
    env: Env, db: Database, client: TestClient
) -> None:
    """Worker concurrency and webhook replay must not duplicate commercial sends."""
    env.sql("UPDATE messages SET handled=true WHERE direction='in'")
    tenant, instance, key = _provision(client, env)
    offer_id = _setup_offer(env, tenant)

    for n in range(40):
        result = _webhook(client, instance, key, f"burst-{uuid.uuid4().hex}-{n}", "Preço?")
        assert result["messages"] == 1

    model = SimulatedAiModel(forced=AiReply("{oferta} custa {preco}", offer_id=offer_id))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: run_ai_replies(db, model), range(8)))

    assert sum(s.replied for s in results) == 1
    assert len(model.calls) == 1
    assert env.sql(
        "SELECT count(*) FROM messages WHERE tenant_id=%s AND direction='in' AND handled=false",
        (tenant,),
    ) == [(0,)]
    assert env.sql(
        "SELECT count(*) FROM messages WHERE tenant_id=%s AND direction='out'",
        (tenant,),
    ) == [(1,)]
