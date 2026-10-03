"""Vendedor IA: respostas só com dados do cadastro e transferência para pessoa nos limites."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from fm_seller.ai.model import AiReply, SimulatedAiModel, UnavailableAiModel
from fm_seller.ai.seller import MAX_BOT_REPLIES_PER_HOUR, render_reply, run_ai_replies
from fm_seller.channels.whatsapp import upsert_conversation
from fm_seller.db import Database
from tests.conftest import Env, unique_email

PHONE = "5511944443333"
LINK = "https://pay.example.test/curso-x"


class Setup:
    def __init__(self, env: Env, db: Database) -> None:
        env.sql("UPDATE messages SET handled = true WHERE direction = 'in'")  # fila global
        self.env, self.db = env, db
        self.tid = env.tenant("Loja IA", unique_email("ia"))
        self.tenant = uuid.UUID(self.tid)
        env.sql(
            "INSERT INTO tenant_settings (tenant_id, ai_enabled, ai_persona) "
            "VALUES (%s, true, 'Simpático')",
            (self.tid,),
        )
        env.sql(
            "INSERT INTO offers (tenant_id, name, description, price_cents, payment_url) "
            "VALUES (%s, 'Curso X', 'Aprenda X', 19700, %s)",
            (self.tid, LINK),
        )
        with db.tx(tenant_id=self.tenant) as conn:
            _, self.conv = upsert_conversation(conn, self.tenant, PHONE, "Dani Silva")

    def say(self, body: str) -> None:
        self.env.sql(
            "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status, "
            "handled) VALUES (%s, %s, 'in', 'customer', %s, 'received', false)",
            (self.tid, self.conv, body),
        )

    def outbound(self) -> list[tuple[str, str, str]]:
        return self.env.sql(
            "SELECT author, body, status FROM messages WHERE conversation_id = %s "
            "AND direction = 'out' ORDER BY created_at",
            (self.conv,),
        )

    def conv_state(self) -> tuple[str, str | None]:
        return self.env.sql(
            "SELECT status, handoff_reason FROM conversations WHERE id = %s", (self.conv,)
        )[0]


@pytest.fixture
def s(env: Env, db: Database) -> Setup:
    return Setup(env, db)


def test_price_and_link_come_from_registry_not_from_the_model(s: Setup, db: Database) -> None:
    s.say("Quanto custa o Curso X?")
    stats = run_ai_replies(db, SimulatedAiModel(), now=datetime.now(UTC))
    assert stats.replied == 1
    assert s.outbound() == [("bot", "Curso X custa R$ 197,00. Quer que eu envie o link?", "queued")]
    s.say("quero comprar")
    run_ai_replies(db, SimulatedAiModel())
    assert LINK in s.outbound()[-1][1]


def test_context_has_no_credentials_and_includes_history(s: Setup, db: Database) -> None:
    s.say("oi")
    model = SimulatedAiModel()
    run_ai_replies(db, model)
    ctx = model.calls[0]
    assert ctx.persona == "Simpático" and ctx.customer_name == "Dani"
    assert ctx.offers[0].price_text == "R$ 197,00"
    assert ctx.history[-1] == ("customer", "oi")
    assert "EAAG" not in repr(ctx) and "access_token" not in repr(ctx)


@pytest.mark.parametrize(
    "text",
    [
        "Custa R$ 99,00 hoje",
        "Compre em https://golpe.example/x",
        "Acesse www.golpe.example",
        "Só 10 reais!",
        "Use {preco} sem oferta {foo}",
    ],
)
def test_invented_price_or_link_is_refused_and_handed_to_a_person(
    s: Setup, db: Database, text: str
) -> None:
    s.say("oi")
    run_ai_replies(db, SimulatedAiModel(forced=AiReply(text, offer_id=None)))
    assert s.conv_state() == ("human", "resposta_invalida")
    assert [b for _, b, _ in s.outbound()] == [
        "Certo! Vou chamar uma pessoa da equipe para continuar com você."
    ]


def test_placeholder_with_unknown_offer_is_refused() -> None:
    reply = AiReply("Custa {preco}", offer_id=str(uuid.uuid4()))
    assert render_reply(reply, {"x": ("n", "R$ 1,00", "https://a.example")}) is None
    ok = render_reply(
        AiReply("Leve {oferta}: {link}", "x"), {"x": ("N", "R$ 1,00", "https://a.example")}
    )
    assert ok == "Leve N: https://a.example"


def test_asking_for_a_person_hands_off_without_calling_the_model(s: Setup, db: Database) -> None:
    s.say("Quero falar com um atendente")
    model = SimulatedAiModel()
    run_ai_replies(db, model)
    assert model.calls == [] and s.conv_state() == ("human", "pediu_atendente")


def test_ai_off_or_unavailable_sends_conversation_to_a_person_silently(
    s: Setup, db: Database
) -> None:
    s.env.sql("UPDATE tenant_settings SET ai_enabled = false WHERE tenant_id = %s", (s.tid,))
    s.say("oi")
    run_ai_replies(db, SimulatedAiModel())
    assert s.conv_state() == ("human", "ia_desligada") and s.outbound() == []
    s.env.sql("UPDATE tenant_settings SET ai_enabled = true WHERE tenant_id = %s", (s.tid,))
    s.env.sql("UPDATE conversations SET status = 'bot' WHERE id = %s", (s.conv,))
    s.say("oi de novo")
    run_ai_replies(db, UnavailableAiModel())
    assert s.conv_state() == ("human", "ia_indisponivel") and s.outbound() == []


def test_no_active_offers_hands_off(s: Setup, db: Database) -> None:
    s.env.sql("UPDATE offers SET active = false WHERE tenant_id = %s", (s.tid,))
    s.say("oi")
    run_ai_replies(db, SimulatedAiModel())
    assert s.conv_state() == ("human", "sem_ofertas")


def test_suppressed_contact_gets_no_reply(s: Setup, db: Database) -> None:
    s.env.sql(
        "INSERT INTO suppressions (tenant_id, identity, reason) VALUES (%s, %s, 'opt_out')",
        (s.tid, PHONE),
    )
    s.say("oi")
    stats = run_ai_replies(db, SimulatedAiModel())
    assert stats.ignored == 1 and s.outbound() == []


def test_several_messages_get_one_answer_and_are_not_handled_twice(s: Setup, db: Database) -> None:
    for t in ("oi", "tem desconto?", "e o preço?"):
        s.say(t)
    model = SimulatedAiModel()
    run_ai_replies(db, model)
    run_ai_replies(db, model)
    assert len(model.calls) == 1 and len(s.outbound()) == 1


def test_reply_rate_limit_hands_off(s: Setup, db: Database) -> None:
    for i in range(MAX_BOT_REPLIES_PER_HOUR):
        s.env.sql(
            "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status) "
            "VALUES (%s, %s, 'out', 'bot', %s, 'sent')",
            (s.tid, s.conv, f"r{i}"),
        )
    s.say("oi")
    run_ai_replies(db, SimulatedAiModel())
    assert s.conv_state() == ("human", "limite_de_respostas")


def test_conversation_in_human_mode_is_left_alone(s: Setup, db: Database) -> None:
    s.env.sql("UPDATE conversations SET status = 'human' WHERE id = %s", (s.conv,))
    s.say("oi")
    model = SimulatedAiModel()
    assert run_ai_replies(db, model).conversations == 0 and model.calls == []
