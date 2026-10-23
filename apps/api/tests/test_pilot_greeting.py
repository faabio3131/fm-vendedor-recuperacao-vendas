"""Fail-closed controls for the authorized single-contact FM greeting pilot."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, cast

import pytest

from fm_seller.ai.gemini import build_system_prompt
from fm_seller.ai.model import AiContext, AiReply, SimulatedAiModel
from fm_seller.ai.seller import run_ai_replies
from fm_seller.channels.evolution_runtime import RoutedWhatsAppSender
from fm_seller.channels.outbox import flush_outbox
from fm_seller.channels.pilot_greeting import (
    PilotGreetingSender,
    greeting_only,
    pilot_contact,
)
from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.recovery.senders import OutboundMessage, SendError, SimulatedSender
from fm_seller.security.crypto import SecretBox
from tests.conftest import Env, unique_email
from tests.test_evolution_runtime import FakeSendClient

PHONE = "5511944443333"


def _pilot(tid: uuid.UUID, phone: str = PHONE) -> Settings:
    return Settings.model_construct(
        pilot_greeting_enabled=True, pilot_greeting_tenant_id=str(tid), pilot_greeting_phone=phone
    )


def test_pilot_gate_exact_tenant_number_and_greetings() -> None:
    tid = uuid.uuid4()
    settings = _pilot(tid)
    assert pilot_contact(settings, tid, PHONE)
    assert not pilot_contact(settings, uuid.uuid4(), PHONE)
    assert not pilot_contact(settings, tid, "5511977008899")
    assert not pilot_contact(Settings.model_construct(pilot_greeting_enabled=False), tid, PHONE)
    assert greeting_only("Oi!")
    assert greeting_only("oi")
    assert greeting_only("bom dia")
    assert not greeting_only("qual o preço?")
    assert not greeting_only("SAIR")
    assert not greeting_only("oi, mande o pix")


def test_pilot_sender_restricts_recipient_instance_and_content() -> None:
    tid = uuid.uuid4()
    fake = FakeSendClient()
    sender = PilotGreetingSender(cast(Any, fake), tenant_id=tid, phone=PHONE)
    cfg = {
        "__provider": "evolution",
        "instance_name": "fmav-" + hashlib.sha256(tid.bytes).hexdigest()[:32],
    }
    route = RoutedWhatsAppSender(
        SimulatedSender(),
        sender,
        meta_enabled=False,
        evolution_enabled=True,
        pilot_tenant_id=tid,
        pilot_phone=PHONE,
    )
    assert route.pilot_only
    assert route.send_text(cfg, PHONE, "Olá, sou a AnaClara!") == "ABC123"
    for config, phone, text in (
        (cfg, "5511977008899", "Oi"),
        ({**cfg, "instance_name": "fmav-" + "a" * 32}, PHONE, "Oi"),
        ({**cfg, "__provider": "whatsapp_cloud"}, PHONE, "Oi"),
        (cfg, PHONE, "Compre por https://example.test"),
        (cfg, PHONE, "Envie uma chave Pix"),
    ):
        with pytest.raises(SendError):
            route.send_text(config, phone, text)
    with pytest.raises(SendError):
        route.send(cfg, OutboundMessage(tid, PHONE, "test", "Oi"))
    assert fake.sent == [{"number": PHONE, "text": "Olá, sou a AnaClara!"}]


def test_pilot_can_greet_without_offer_or_global_ai(env: Env, db: Database) -> None:
    env.sql("UPDATE messages SET handled=true WHERE direction='in'")
    tid = uuid.UUID(env.tenant("Piloto FM", unique_email("pilot-greet")))
    env.sql(
        "INSERT INTO tenant_settings (tenant_id, ai_enabled, ai_persona) "
        "VALUES (%s,false,'AnaClara')",
        (str(tid),),
    )
    from fm_seller.channels.whatsapp import upsert_conversation

    with db.tx(tenant_id=tid) as conn:
        _, cv = upsert_conversation(conn, tid, PHONE, "Teste")
    env.sql(
        "INSERT INTO messages (tenant_id,conversation_id,direction,author,body,status,handled) "
        "VALUES (%s,%s,'in','customer','oi','received',false)",
        (str(tid), cv),
    )
    model = SimulatedAiModel(forced=AiReply("Olá, sou a AnaClara!"))
    stats = run_ai_replies(db, model, pilot_settings=_pilot(tid))
    assert stats.replied == 1
    assert model.calls and model.calls[0].offers == ()
    assert model.calls[0].pilot_greeting
    assert model.calls[0].history == (("customer", "oi"),)
    assert env.sql(
        "SELECT body,status FROM messages WHERE conversation_id=%s AND direction='out'", (cv,)
    ) == [("Olá, sou a AnaClara!", "queued")]
    assert env.sql("SELECT status FROM conversations WHERE id=%s", (cv,)) == [("bot",)]


def test_nonpilot_stays_disabled_even_when_pilot_enabled(env: Env, db: Database) -> None:
    env.sql("UPDATE messages SET handled=true WHERE direction='in'")
    tid = uuid.UUID(env.tenant("Outro contato", unique_email("pilot-deny")))
    env.sql("INSERT INTO tenant_settings(tenant_id,ai_enabled) VALUES (%s,false)", (str(tid),))
    from fm_seller.channels.whatsapp import upsert_conversation

    with db.tx(tenant_id=tid) as conn:
        _, cv = upsert_conversation(conn, tid, PHONE, "Cliente")
    env.sql(
        "INSERT INTO messages(tenant_id,conversation_id,direction,author,body,status,handled) "
        "VALUES (%s,%s,'in','customer','oi','received',false)",
        (str(tid), cv),
    )
    model = SimulatedAiModel(forced=AiReply("Oi"))
    stats = run_ai_replies(db, model, pilot_settings=_pilot(uuid.uuid4()))
    assert stats.handoffs == 1
    assert model.calls == []
    assert env.sql("SELECT status,handoff_reason FROM conversations WHERE id=%s", (cv,)) == [
        ("human", "ia_desligada")
    ]


def test_outbox_does_not_claim_other_contacts_in_pilot(env: Env, db: Database) -> None:
    env.sql("UPDATE messages SET status='failed' WHERE direction='out' AND status='queued'")
    tid = uuid.UUID(env.tenant("Fila piloto", unique_email("pilot-queue")))
    from fm_seller.channels.whatsapp import upsert_conversation

    with db.tx(tenant_id=tid) as conn:
        _, cv = upsert_conversation(conn, tid, "5511977001100", "Outro")
    env.sql(
        "INSERT INTO messages(tenant_id,conversation_id,direction,author,body,status) "
        "VALUES (%s,%s,'out','bot','Nao deve sair','queued')",
        (str(tid), cv),
    )
    fake = FakeSendClient()
    pilot = PilotGreetingSender(cast(Any, fake), tenant_id=tid, phone=PHONE)
    route = RoutedWhatsAppSender(
        SimulatedSender(),
        pilot,
        meta_enabled=False,
        evolution_enabled=True,
        pilot_tenant_id=tid,
        pilot_phone=PHONE,
    )
    stats = flush_outbox(db, SecretBox(SecretBox.generate_key_spec()), route)
    assert stats.claimed == 0
    assert env.sql("SELECT status FROM messages WHERE conversation_id=%s", (cv,)) == [("queued",)]
    assert fake.sent == []


def test_pilot_gemini_prompt_removes_selling_instructions() -> None:
    ctx = AiContext(
        persona="Ana Clara, assistente virtual da FM Tecnologia",
        customer_name="",
        offers=(),
        history=(("customer", "oi"),),
        pilot_greeting=True,
    )
    prompt = build_system_prompt(ctx)
    assert "saudação" in prompt
    assert "não é venda" in prompt
    assert "Ana Clara" in prompt
    assert "ESTILO DA LOJA" not in prompt
    assert "Só fale dos produtos listados" not in prompt


def test_pilot_outbox_rejects_old_queue_and_human_conversation(env: Env, db: Database) -> None:
    from fm_seller.channels.whatsapp import upsert_conversation

    tid = uuid.UUID(env.tenant("Fila antiga", unique_email("pilot-old")))
    with db.tx(tenant_id=tid) as conn:
        _, cv = upsert_conversation(conn, tid, PHONE, "Teste")
    env.sql("UPDATE conversations SET status='bot', last_inbound_at=now() WHERE id=%s", (cv,))
    env.sql(
        "INSERT INTO messages (tenant_id,conversation_id,direction,author,body,status,created_at) "
        "VALUES (%s,%s,'out','bot','Saudação antiga','queued',now()-interval '1 hour')",
        (str(tid), cv),
    )
    fake = FakeSendClient()
    sender = RoutedWhatsAppSender(
        SimulatedSender(),
        PilotGreetingSender(cast(Any, fake), tenant_id=tid, phone=PHONE),
        meta_enabled=False,
        evolution_enabled=True,
        pilot_tenant_id=tid,
        pilot_phone=PHONE,
    )
    assert flush_outbox(db, SecretBox(SecretBox.generate_key_spec()), sender).claimed == 0
    env.sql("UPDATE messages SET created_at=now() WHERE conversation_id=%s", (cv,))
    env.sql("UPDATE conversations SET status='human' WHERE id=%s", (cv,))
    assert flush_outbox(db, SecretBox(SecretBox.generate_key_spec()), sender).claimed == 0
    assert fake.sent == []
