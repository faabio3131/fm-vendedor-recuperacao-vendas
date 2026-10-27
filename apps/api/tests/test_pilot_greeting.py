"""Fail-closed controls for the authorized single-contact FM greeting pilot."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any, cast

import pytest

from fm_seller.ai.gemini import build_system_prompt
from fm_seller.ai.model import AiContext, AiReply, SimulatedAiModel
from fm_seller.ai.seller import run_ai_replies
from fm_seller.channels.evolution_runtime import RoutedWhatsAppSender
from fm_seller.channels.outbound import HANDOFF_MESSAGE
from fm_seller.channels.outbox import flush_outbox
from fm_seller.channels.pilot_greeting import (
    DEMO_VOLUME_QUESTION,
    PilotDemoSender,
    PilotGreetingSender,
    demo_plan_listing_requested,
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
        persona="Ana Clara, assistente virtual da FM Tecnologia IA",
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


def test_pilot_demo_uses_live_commercial_plans_with_global_ai_off(env: Env, db: Database) -> None:
    from fm_seller.channels.pilot_greeting import DEMO_PAGE
    from fm_seller.channels.whatsapp import upsert_conversation

    env.sql("UPDATE messages SET handled=true WHERE direction='in'")
    tid = uuid.UUID(env.tenant("FM demo de vendas", unique_email("pilot-sales")))
    env.sql(
        "INSERT INTO tenant_settings (tenant_id,ai_enabled,ai_persona) "
        "VALUES (%s,false,'AnaClara')",
        (str(tid),),
    )
    with db.tx(tenant_id=tid) as conn:
        _, cv = upsert_conversation(conn, tid, PHONE, "Cliente QA")
    env.sql(
        "INSERT INTO messages(tenant_id,conversation_id,direction,author,body,status,handled) "
        "VALUES (%s,%s,'in','customer','Quais planos estão disponíveis?','received',false)",
        (str(tid), cv),
    )
    model = SimulatedAiModel(
        forced=AiReply("{oferta} custa {preco} por mês. Veja {link}", offer_id="av-essencial")
    )
    settings = _pilot(tid)
    settings.pilot_demo_enabled = True
    stats = run_ai_replies(db, model, pilot_settings=settings)
    assert stats.replied == 1
    assert len(model.calls) == 1
    ctx = model.calls[0]
    assert ctx.pilot_demo and not ctx.pilot_greeting
    assert {o.name for o in ctx.offers} == {"Essencial", "Profissional", "Escala"}
    assert env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s AND direction='out'",
        (cv,),
    ) == [("Essencial custa R$ 129,90 por mês. Veja " + DEMO_PAGE,)]


def test_pilot_demo_lists_all_live_plans_without_model_inventing_prices(
    env: Env, db: Database
) -> None:
    from fm_seller.channels.pilot_greeting import DEMO_PAGE
    from fm_seller.channels.whatsapp import upsert_conversation
    from fm_seller.money import format_brl

    env.sql("UPDATE messages SET handled=true WHERE direction='in'")
    tid = uuid.UUID(env.tenant("FM catálogo demonstrativo", unique_email("pilot-multiplan")))
    env.sql(
        "INSERT INTO tenant_settings (tenant_id, ai_enabled, ai_persona) "
        "VALUES (%s,false,'AnaClara')",
        (str(tid),),
    )
    with db.tx(tenant_id=tid) as conn:
        _, cv = upsert_conversation(conn, tid, PHONE, "Cliente QA")
    env.sql(
        "INSERT INTO messages (tenant_id,conversation_id,direction,author,body,status,handled) "
        "VALUES (%s,%s,'in','customer','Quais são os planos e quais os valores?',"
        "'received',false)",
        (str(tid), cv),
    )
    model = SimulatedAiModel(forced=AiReply("R$ 1,00, compre em https://nao-aprovado.test"))
    settings = _pilot(tid)
    settings.pilot_demo_enabled = True
    stats = run_ai_replies(db, model, pilot_settings=settings)
    assert stats.replied == 1 and stats.handoffs == 0
    assert not model.calls  # A listagem de preços não depende de criatividade do modelo.
    sent = env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s AND direction='out'",
        (cv,),
    )
    assert len(sent) == 1
    body = sent[0][0]
    rows = env.sql(
        "SELECT display_name, price_monthly_cents, limits->>'ai_replies_per_month' "
        "FROM plans WHERE key IN ('av-essencial','av-profissional','av-escala') "
        "AND active ORDER BY phase"
    )
    assert len(rows) == 3 and DEMO_PAGE in body and len(body) <= 850
    for name, cents, quota in rows:
        assert name in body
        assert format_brl(cents) in body
        assert f"{int(quota):,}".replace(",", ".") in body
    assert "https://nao-aprovado.test" not in body

    fake = FakeSendClient()
    pilot = PilotDemoSender(cast(Any, fake), tenant_id=tid, phone=PHONE)
    cfg = {
        "__provider": "evolution",
        "instance_name": "fmav-" + hashlib.sha256(tid.bytes).hexdigest()[:32],
        "__demo_prices": "|".join(format_brl(r[1]) for r in rows),
        "__demo_quotas": "|".join(
            str(r[2]) + "|" + f"{int(r[2]):,}".replace(",", ".") for r in rows
        ),
    }
    assert pilot.send_text(cfg, PHONE, body) == "ABC123"
    assert len(fake.sent) == 1


def test_pilot_demo_prices_ignore_exhausted_model_quota_but_keep_pilot_scope(
    env: Env, db: Database
) -> None:
    from fm_seller.ai.model import UnavailableAiModel
    from fm_seller.channels.whatsapp import upsert_conversation

    env.sql("UPDATE messages SET handled=true WHERE direction='in'")
    tid = uuid.UUID(env.tenant("FM demo sem tokens", unique_email("pilot-zero-tokens")))
    env.sql(
        "INSERT INTO tenant_settings(tenant_id,ai_enabled,ai_persona) VALUES (%s,false,'AnaClara')",
        (str(tid),),
    )
    env.sql(
        "INSERT INTO ai_usage(tenant_id,day,calls,failures,tokens_in,tokens_out) "
        "VALUES (%s,(now() AT TIME ZONE 'America/Sao_Paulo')::date,100000,0,0,0)",
        (str(tid),),
    )
    with db.tx(tenant_id=tid) as conn:
        _, cv = upsert_conversation(conn, tid, PHONE, "Contato autorizado")
    env.sql(
        "INSERT INTO messages(tenant_id,conversation_id,direction,author,body,status,handled) "
        "VALUES (%s,%s,'in','customer','Quais são os planos e valores?',"
        "'received',false)",
        (str(tid), cv),
    )
    settings = _pilot(tid)
    settings.pilot_demo_enabled = True
    assert run_ai_replies(db, UnavailableAiModel(), pilot_settings=settings).replied == 1
    assert env.sql(
        "SELECT calls,tokens_in,tokens_out FROM ai_usage WHERE tenant_id=%s", (str(tid),)
    ) == [(100000, 0, 0)]
    outgoing = env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s AND direction='out'", (cv,)
    )
    assert len(outgoing) == 1
    assert all(n in outgoing[0][0] for n in ("Essencial", "Profissional", "Escala"))
    assert env.sql("SELECT status FROM conversations WHERE id=%s", (cv,)) == [("bot",)]


def test_pilot_demo_plan_listing_intent_is_narrow() -> None:
    assert demo_plan_listing_requested("Quais são os planos e quais os valores?")
    assert demo_plan_listing_requested("Lista de planos e preços")
    assert not demo_plan_listing_requested("Oi!")
    assert not demo_plan_listing_requested("Quais planos estão disponíveis?")
    assert not demo_plan_listing_requested("Quero falar com uma pessoa")
    assert not demo_plan_listing_requested("Quero informações sobre o Essencial")


@pytest.mark.parametrize(
    ("volume", "expected_plan", "expected_price", "expected_quota"),
    [
        ("800", "Profissional", "R$ 199,90", "1.000"),
        ("2.000", "Escala", "R$ 399,90", "3.000"),
        ("2000", "Escala", "R$ 399,90", "3.000"),
        ("2 mil", "Escala", "R$ 399,90", "3.000"),
    ],
)
def test_realistic_plan_advice_asks_volume_then_recommends_without_ai_tokens(
    env: Env,
    db: Database,
    volume: str,
    expected_plan: str,
    expected_price: str,
    expected_quota: str,
) -> None:
    from fm_seller.ai.model import UnavailableAiModel
    from fm_seller.channels.whatsapp import upsert_conversation

    env.sql("UPDATE messages SET handled=true WHERE direction='in'")
    tid = uuid.UUID(env.tenant("FM consultiva", unique_email("pilot-advice")))
    env.sql(
        "INSERT INTO tenant_settings(tenant_id,ai_enabled,ai_persona) VALUES (%s,false,'AnaClara')",
        (str(tid),),
    )
    with db.tx(tenant_id=tid) as conn:
        _, cv = upsert_conversation(conn, tid, PHONE, "Contato autorizado")
    settings = _pilot(tid)
    settings.pilot_demo_enabled = True

    def ask(body: str) -> None:
        env.sql(
            "INSERT INTO messages(tenant_id,conversation_id,direction,author,body,status,handled) "
            "VALUES (%s,%s,'in','customer',%s,'received',false)",
            (str(tid), cv, body),
        )

    model = UnavailableAiModel()
    ask("Qual plano que recomenda pra mim?")
    first = run_ai_replies(db, model, pilot_settings=settings)
    assert first.replied == 1 and first.handoffs == 0
    assert env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s AND direction='out'",
        (cv,),
    ) == [(DEMO_VOLUME_QUESTION,)]
    ask(volume)
    second = run_ai_replies(db, model, pilot_settings=settings)
    assert second.replied == 1 and second.handoffs == 0
    sent = env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s AND direction='out' "
        "ORDER BY created_at",
        (cv,),
    )
    assert len(sent) == 2
    assert expected_plan in sent[1][0] and expected_price in sent[1][0]
    assert f"{expected_quota} respostas" in sent[1][0]
    assert env.sql("SELECT status FROM conversations WHERE id=%s", (cv,)) == [("bot",)]
    assert env.sql("SELECT count(*) FROM ai_usage WHERE tenant_id=%s", (str(tid),)) == [(0,)]
    allowed = PilotDemoSender(cast(Any, FakeSendClient()), tenant_id=tid, phone=PHONE)
    cfg = {
        "__provider": "evolution",
        "instance_name": "fmav-" + hashlib.sha256(tid.bytes).hexdigest()[:32],
        "__demo_prices": "R$ 129,90|R$ 199,90|R$ 399,90",
        "__demo_quotas": "500|1.000|3.000|1000|3000",
    }
    assert allowed.send_text(cfg, PHONE, sent[1][0]) == "ABC123"
    # Actual observed failure: "Quero" after the plan recommendation must not
    # produce resposta_invalida/handoff just because Gemini writes a raw price.
    ask("Quero")
    third = run_ai_replies(db, model, pilot_settings=settings)
    assert third.replied == 1 and third.handoffs == 0
    details = env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s AND direction='out' "
        "ORDER BY created_at DESC LIMIT 1",
        (cv,),
    )[0][0]
    assert expected_plan in details and expected_price in details
    assert f"{expected_quota} respostas" in details
    assert "não são respostas ilimitadas" in details.lower()
    assert allowed.send_text(cfg, PHONE, details) == "ABC123"
    # Follow-up should preserve the previously recommended tier, without
    # asking for a person or consuming any language-model tokens.
    ask("O que esse plano faz?")
    fourth = run_ai_replies(db, model, pilot_settings=settings)
    assert fourth.replied == 1 and fourth.handoffs == 0
    features = env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s AND direction='out' "
        "ORDER BY created_at DESC LIMIT 1",
        (cv,),
    )[0][0]
    assert "Vendedor IA" in features and "Conversas" in features
    assert allowed.send_text(cfg, PHONE, features) == "ABC123"
    ask("Como contratar?")
    fifth = run_ai_replies(db, model, pilot_settings=settings)
    assert fifth.replied == 1 and fifth.handoffs == 0
    contract = env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s AND direction='out' "
        "ORDER BY created_at DESC LIMIT 1",
        (cv,),
    )[0][0]
    assert "contratação é assistida" in contract
    assert allowed.send_text(cfg, PHONE, contract) == "ABC123"
    ask("O que esse plano não faz?")
    sixth = run_ai_replies(db, model, pilot_settings=settings)
    assert sixth.replied == 1 and sixth.handoffs == 0
    exclusions = env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s AND direction='out' "
        "ORDER BY created_at DESC LIMIT 1",
        (cv,),
    )[0][0]
    assert "não oferece respostas de IA ilimitadas" in exclusions
    assert "não ativa assinatura automaticamente" in exclusions
    assert allowed.send_text(cfg, PHONE, exclusions) == "ABC123"
    assert env.sql("SELECT status FROM conversations WHERE id=%s", (cv,)) == [("bot",)]
    assert env.sql("SELECT count(*) FROM ai_usage WHERE tenant_id=%s", (str(tid),)) == [(0,)]


def test_pilot_voice_transcript_with_inbound_messages_volume_qualifies_before_pricing(
    env: Env, db: Database
) -> None:
    """A real-world audio transcript must not trigger unsafe model price speculation."""
    from fm_seller.ai.model import UnavailableAiModel
    from fm_seller.channels.whatsapp import upsert_conversation

    env.sql("UPDATE messages SET handled=true WHERE direction='in'")
    tid = uuid.UUID(env.tenant("FM voz consultiva", unique_email("pilot-voice-volume")))
    env.sql(
        "INSERT INTO tenant_settings(tenant_id,ai_enabled,ai_persona) VALUES (%s,false,'AnaClara')",
        (str(tid),),
    )
    with db.tx(tenant_id=tid) as conn:
        _, conv = upsert_conversation(conn, tid, PHONE, "Contato autorizado")
    settings = _pilot(tid)
    settings.pilot_demo_enabled = True

    def say(body: str) -> None:
        env.sql(
            "INSERT INTO messages(tenant_id,conversation_id,direction,author,body,"
            "status,handled) VALUES (%s,%s,'in','customer',%s,'received',false)",
            (str(tid), conv, body),
        )

    model = UnavailableAiModel()
    say("Ana Clara, tenho uma loja, eu recebo 2000 mensagens em média por mês de clientes.")
    first = run_ai_replies(db, model, pilot_settings=settings)
    assert first.replied == 1 and first.handoffs == 0
    sent = env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s AND direction='out'", (conv,)
    )[0][0]
    assert "franquia dos planos conta respostas de IA" in sent
    assert "quantas dessas mensagens" in sent
    assert "R$" not in sent
    assert env.sql("SELECT status,handoff_reason FROM conversations WHERE id=%s", (conv,)) == [
        ("bot", None)
    ]

    say("Gostaria de 2000 respostas de IA por mês")
    second = run_ai_replies(db, model, pilot_settings=settings)
    assert second.replied == 1 and second.handoffs == 0
    sent = env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s AND direction='out' "
        "ORDER BY created_at DESC LIMIT 1",
        (conv,),
    )[0][0]
    assert "Escala" in sent and "R$ 399,90" in sent
    assert env.sql("SELECT status FROM conversations WHERE id=%s", (conv,)) == [("bot",)]
    assert env.sql("SELECT count(*) FROM ai_usage WHERE tenant_id=%s", (str(tid),)) == [(0,)]


@pytest.mark.parametrize(
    ("answer", "expected_plan", "expected_quota"),
    [("Todas", "Escala", "3.000"), ("todas elas", "Escala", "3.000"), ("400", "Essencial", "500")],
)
def test_pilot_clarification_answer_reuses_stated_volume_without_model(
    env: Env, db: Database, answer: str, expected_plan: str, expected_quota: str
) -> None:
    """Teste real de 26/10: '1500 mensagens' -> esclarecimento -> 'Todas' caia no modelo,
    que repetia 1.500 e era barrado pelo envio demo ('número não autorizado')."""
    from fm_seller.ai.model import UnavailableAiModel
    from fm_seller.channels.whatsapp import upsert_conversation

    env.sql("UPDATE messages SET handled=true WHERE direction='in'")
    tid = uuid.UUID(env.tenant("FM todas", unique_email("pilot-todas")))
    env.sql(
        "INSERT INTO tenant_settings(tenant_id,ai_enabled,ai_persona) VALUES (%s,false,'AnaClara')",
        (str(tid),),
    )
    with db.tx(tenant_id=tid) as conn:
        _, conv = upsert_conversation(conn, tid, PHONE, "Contato autorizado")
    settings = _pilot(tid)
    settings.pilot_demo_enabled = True

    def say(body: str) -> None:
        env.sql(
            "INSERT INTO messages(tenant_id,conversation_id,direction,author,body,"
            "status,handled) VALUES (%s,%s,'in','customer',%s,'received',false)",
            (str(tid), conv, body),
        )

    model = UnavailableAiModel()
    say("Eu recebo 1500 mensagens por mês")
    assert run_ai_replies(db, model, pilot_settings=settings).replied == 1
    say(answer)
    result = run_ai_replies(db, model, pilot_settings=settings)
    assert result.replied == 1 and result.handoffs == 0
    sent = env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s AND direction='out' "
        "ORDER BY created_at DESC LIMIT 1",
        (conv,),
    )[0][0]
    assert expected_plan in sent and f"{expected_quota} respostas" in sent
    assert env.sql("SELECT status FROM conversations WHERE id=%s", (conv,)) == [("bot",)]
    assert env.sql("SELECT count(*) FROM ai_usage WHERE tenant_id=%s", (str(tid),)) == [(0,)]


def test_pilot_hello_is_safe_and_does_not_handoff_even_after_an_earlier_turn(
    env: Env, db: Database
) -> None:
    from fm_seller.ai.model import UnavailableAiModel
    from fm_seller.channels.whatsapp import upsert_conversation

    env.sql("UPDATE messages SET handled=true WHERE direction='in'")
    tid = uuid.UUID(env.tenant("FM saudacao real", unique_email("pilot-hello-resume")))
    env.sql(
        "INSERT INTO tenant_settings(tenant_id,ai_enabled,ai_persona) VALUES (%s,false,'AnaClara')",
        (str(tid),),
    )
    with db.tx(tenant_id=tid) as conn:
        _, cv = upsert_conversation(conn, tid, PHONE, "Contato autorizado")
    pilot = _pilot(tid)
    pilot.pilot_demo_enabled = True

    for hello in ("Olá", "Oi"):
        env.sql(
            "INSERT INTO messages(tenant_id,conversation_id,direction,author,body,"
            "status,handled) VALUES (%s,%s,'in','customer',%s,'received',false)",
            (str(tid), cv, hello),
        )
        stats = run_ai_replies(db, UnavailableAiModel(), pilot_settings=pilot)
        assert stats.replied == 1 and stats.handoffs == 0
        assert env.sql("SELECT status,handoff_reason FROM conversations WHERE id=%s", (cv,)) == [
            ("bot", None)
        ]
    assert (
        env.sql(
            "SELECT body FROM messages WHERE conversation_id=%s AND direction='out' "
            "ORDER BY created_at",
            (cv,),
        )
        == [
            (
                "Olá! Sou a AnaClara, assistente virtual da FM Tecnologia IA. "
                "Como posso ajudar você com o AtendeVendeIA?",
            )
        ]
        * 2
    )
    assert env.sql("SELECT count(*) FROM ai_usage WHERE tenant_id=%s", (str(tid),)) == [(0,)]


def test_pilot_resumes_batched_audio_and_text_after_manual_handoff(env: Env, db: Database) -> None:
    """Preserve human handoff until manual resume, then answer last qualified request."""
    from fm_seller.ai.model import UnavailableAiModel
    from fm_seller.channels.whatsapp import upsert_conversation

    env.sql("UPDATE messages SET handled=true WHERE direction='in'")
    tid = uuid.UUID(env.tenant("FM teste em lote", unique_email("pilot-batched-resume")))
    env.sql(
        "INSERT INTO tenant_settings(tenant_id,ai_enabled,ai_persona) VALUES (%s,false,'AnaClara')",
        (str(tid),),
    )
    with db.tx(tenant_id=tid) as conn:
        _, cv = upsert_conversation(conn, tid, PHONE, "Contato autorizado")
    pilot = _pilot(tid)
    pilot.pilot_demo_enabled = True
    env.sql(
        "UPDATE conversations SET status='human',handoff_reason='resposta_invalida' WHERE id=%s",
        (cv,),
    )

    incoming = (
        ("Olá", None),
        ("Mas ele vai pagar assim, ó?", "done"),
        ("Recebo 2.000 mensagens por mês. Qual plano você recomenda?", "done"),
    )
    for body, media_status in incoming:
        env.sql(
            "INSERT INTO messages(tenant_id,conversation_id,direction,author,body,"
            "status,handled,media_status) "
            "VALUES (%s,%s,'in','customer',%s,'received',false,%s)",
            (str(tid), cv, body, media_status),
        )
    inactive = run_ai_replies(db, UnavailableAiModel(), pilot_settings=pilot)
    assert inactive.conversations == 0
    assert env.sql(
        "SELECT count(*) FROM messages WHERE conversation_id=%s AND direction='in' AND NOT handled",
        (cv,),
    ) == [(3,)]
    assert env.sql("SELECT status FROM conversations WHERE id=%s", (cv,)) == [("human",)]

    # Equivalent to the existing owner/admin "Devolver ao vendedor IA" action.
    env.sql("UPDATE conversations SET status='bot',handoff_reason=NULL WHERE id=%s", (cv,))
    active = run_ai_replies(db, UnavailableAiModel(), pilot_settings=pilot)
    assert active.replied == 1 and active.handoffs == 0
    assert env.sql("SELECT status,handoff_reason FROM conversations WHERE id=%s", (cv,)) == [
        ("bot", None)
    ]
    assert env.sql(
        "SELECT count(*) FROM messages WHERE conversation_id=%s AND direction='in' AND NOT handled",
        (cv,),
    ) == [(0,)]
    outbound = env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s AND direction='out'", (cv,)
    )
    assert len(outbound) == 1
    assert "franquia dos planos conta respostas de IA" in outbound[0][0]
    assert "quantas dessas mensagens" in outbound[0][0]
    assert "R$" not in outbound[0][0]
    assert env.sql("SELECT count(*) FROM ai_usage WHERE tenant_id=%s", (str(tid),)) == [(0,)]


def test_pilot_advice_qualifies_instead_of_guessing() -> None:
    from fm_seller.channels.pilot_greeting import (
        demo_recommendation_requested,
        demo_requested_volume,
    )

    assert demo_recommendation_requested("Qual plano que você recomenda pra mim?")
    assert demo_recommendation_requested("Qual seria o plano ideal?")
    assert not demo_recommendation_requested("Qual o melhor atendimento?")
    assert demo_requested_volume("Preciso de 800 respostas por mês") == 800
    assert demo_requested_volume("1.500 respostas por mês") == 1500
    assert demo_requested_volume("3 vendedores na empresa") is None
    assert demo_requested_volume("800", direct_answer=True) == 800
    assert demo_requested_volume("R$ 800", direct_answer=True) is None


def test_pilot_handoff_notice_is_claimable_only_for_authorized_human_transition(
    env: Env, db: Database
) -> None:
    from fm_seller.channels.whatsapp import upsert_conversation

    env.sql("UPDATE messages SET status='failed' WHERE direction='out' AND status='queued'")
    tid = uuid.UUID(env.tenant("Piloto handoff", unique_email("handoff-notice")))
    with db.tx(tenant_id=tid) as conn:
        _, cv = upsert_conversation(conn, tid, PHONE, "Contato autorizado")
    env.sql(
        "UPDATE conversations SET status='human', handoff_reason='modelo_pediu', "
        "last_inbound_at=now() WHERE id=%s",
        (cv,),
    )
    env.sql(
        "INSERT INTO messages (tenant_id,conversation_id,direction,author,body,status) "
        "VALUES (%s,%s,'out','bot',%s,'queued')",
        (str(tid), cv, HANDOFF_MESSAGE),
    )
    env.sql(
        "INSERT INTO messages (tenant_id,conversation_id,direction,author,body,status) "
        "VALUES (%s,%s,'out','bot','Resposta antiga proibida','queued')",
        (str(tid), cv),
    )
    fake = FakeSendClient()
    route = RoutedWhatsAppSender(
        SimulatedSender(),
        PilotDemoSender(cast(Any, fake), tenant_id=tid, phone=PHONE),
        meta_enabled=False,
        evolution_enabled=True,
        pilot_tenant_id=tid,
        pilot_phone=PHONE,
    )
    result = flush_outbox(db, SecretBox(SecretBox.generate_key_spec()), route)
    # Claiming is independent of provider credentials; an unavailable connection
    # fails closed but the correct notice no longer hangs in queued forever.
    assert result.claimed == 1 and result.failed == 1
    assert env.sql(
        "SELECT status FROM messages WHERE conversation_id=%s AND body=%s",
        (cv, HANDOFF_MESSAGE),
    ) == [("failed",)]
    assert env.sql(
        "SELECT status FROM messages WHERE conversation_id=%s AND body='Resposta antiga proibida'",
        (cv,),
    ) == [("queued",)]
    assert fake.sent == []


def test_pilot_demo_sender_denies_other_contact_arbitrary_prices_and_checkout() -> None:
    from fm_seller.channels.pilot_greeting import DEMO_PAGE

    tid = uuid.uuid4()
    fake = FakeSendClient()
    demo = PilotDemoSender(cast(Any, fake), tenant_id=tid, phone=PHONE)
    config = {
        "__provider": "evolution",
        "instance_name": "fmav-" + hashlib.sha256(tid.bytes).hexdigest()[:32],
        "__demo_prices": "R$ 129,90|R$ 199,90|R$ 399,90",
        "__demo_quotas": "500|1.000|3.000|1000|3000",
    }
    assert (
        demo.send_text(config, PHONE, "Essencial: R$ 129,90 e 500 respostas. Veja " + DEMO_PAGE)
        == "ABC123"
    )
    for recipient, body in (
        ("5511977008899", "Olá!"),
        (PHONE, "O preço é R$ 19,90."),
        (PHONE, "10.000 respostas."),
        (PHONE, "Pague em https://fraude.example.test"),
        (PHONE, "Pagamento confirmado!"),
    ):
        with pytest.raises(SendError):
            demo.send_text(config, recipient, body)
    with pytest.raises(SendError):
        demo.send(config, OutboundMessage(tid, PHONE, "marketing", "Promoção"))
    assert len(fake.sent) == 1


def test_pilot_demo_prompt_explains_assisted_sale_only() -> None:
    from fm_seller.ai.model import OfferView

    ctx = AiContext(
        persona="AnaClara",
        customer_name="Cliente",
        offers=(OfferView("av-essencial", "Essencial", "500 respostas", "R$ 129,90"),),
        history=(("customer", "quero conhecer o plano"),),
        pilot_demo=True,
    )
    prompt = build_system_prompt(ctx)
    assert "AnaClara" in prompt
    assert "TOM CADASTRADO" in prompt
    assert "contratação automática" in prompt
    assert "não checkout" in prompt
    assert "av-essencial" in prompt
    assert "500 respostas" in prompt


def test_contabo_compose_forwards_opt_in_demo_to_api_and_worker() -> None:
    """The shared compose environment must forward the gated demo flag."""
    compose = (
        Path(__file__).resolve().parents[3] / "deploy" / "contabo" / "compose.yml"
    ).read_text(encoding="utf-8")
    assert "FM_PILOT_DEMO_ENABLED: ${FM_PILOT_DEMO_ENABLED:-false}" in compose
    assert "FM_EVOLUTION_SEND_ENABLED: ${FM_EVOLUTION_SEND_ENABLED:-false}" in compose
    assert "environment: *app-env" in compose
