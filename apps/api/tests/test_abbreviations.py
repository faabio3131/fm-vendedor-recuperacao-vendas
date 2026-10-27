import uuid

import pytest

from fm_seller.ai.abbreviations import expand_abbreviations
from fm_seller.ai.model import UnavailableAiModel
from fm_seller.ai.seller import knowledge_reply, run_ai_replies
from fm_seller.channels.whatsapp import upsert_conversation
from fm_seller.config import Settings
from fm_seller.db import Database
from tests.conftest import Env, unique_email

PHONE = "5511944443333"


def _pilot(tid: uuid.UUID) -> Settings:
    return Settings.model_construct(
        pilot_greeting_enabled=True, pilot_greeting_tenant_id=str(tid), pilot_greeting_phone=PHONE
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("O q ela faz?", "O que ela faz?"),
        ("vc tem plano? pq preciso", "você tem plano? porque preciso"),
        ("Eu recebo 1500 msg por mês", "Eu recebo 1500 mensagem por mês"),
        ("recebo 1500msgs", "recebo 1500 mensagens"),
        ("qto custa o Escala, pfv", "quanto custa o Escala, por favor"),
        ("quero info", "quero informação"),
    ],
)
def test_common_whatsapp_abbreviations_are_expanded(raw: str, expected: str) -> None:
    assert expand_abbreviations(raw) == expected


def test_ambiguous_or_unknown_forms_are_left_alone() -> None:
    for text in ("n quero", "dnd", "msgem", "qualquer coisa", "Escala"):
        assert expand_abbreviations(text) == text


def test_expanded_question_matches_the_registered_one() -> None:
    items = (("O que é o AtendeVendeIA e o que ele faz?", "Resposta cadastrada."),)
    assert knowledge_reply(expand_abbreviations("o q ela faz?"), items) == "Resposta cadastrada."


def test_abbreviated_question_is_answered_from_knowledge_without_model(
    env: Env, db: Database
) -> None:
    env.sql("UPDATE messages SET handled=true WHERE direction='in'")
    tid = uuid.UUID(env.tenant("FM abrev", unique_email("pilot-abrev")))
    env.sql(
        "INSERT INTO tenant_settings(tenant_id,ai_enabled,ai_persona) VALUES (%s,false,'AnaClara')",
        (str(tid),),
    )
    env.sql(
        "INSERT INTO knowledge_items(tenant_id,question,answer) VALUES (%s,%s,%s)",
        (
            str(tid),
            "Quantas mensagens posso enviar?",
            "Resposta cadastrada de envio.",
        ),
    )
    with db.tx(tenant_id=tid) as conn:
        _, conv = upsert_conversation(conn, tid, PHONE, "Contato autorizado")
    settings = _pilot(tid)
    settings.pilot_demo_enabled = True
    env.sql(
        "INSERT INTO messages(tenant_id,conversation_id,direction,author,body,status,handled) "
        "VALUES (%s,%s,'in','customer',%s,'received',false)",
        (str(tid), conv, "qtas msgs posso enviar?"),
    )
    result = run_ai_replies(db, UnavailableAiModel(), pilot_settings=settings)
    assert result.replied == 1 and result.handoffs == 0
    sent = env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s AND direction='out'", (conv,)
    )
    assert sent == [("Resposta cadastrada de envio.",)]
    assert env.sql("SELECT count(*) FROM ai_usage WHERE tenant_id=%s", (str(tid),)) == [(0,)]
