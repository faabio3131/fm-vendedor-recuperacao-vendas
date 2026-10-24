"""Regression of the authorized AnaClara pilot's real commercial failure on 24 Oct 2026.

No real phones, payments or external providers are used. PostgreSQL executes the
same tenant, plan, quota and sender policies as the existing application.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any, cast

import pytest

from fm_seller.ai.model import UnavailableAiModel
from fm_seller.ai.seller import (
    MAX_BOT_REPLIES_PER_HOUR,
    MAX_PILOT_DEMO_REPLIES_PER_HOUR,
    demo_verified_commercial_reply,
    run_ai_replies,
)
from fm_seller.channels.pilot_greeting import DEMO_PAGE, PilotDemoSender
from fm_seller.channels.whatsapp import upsert_conversation
from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.recovery.senders import SendError
from tests.conftest import Env, unique_email
from tests.test_evolution_runtime import FakeSendClient

PHONE = "5511944443333"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "Esse software ele faz venda? Ele gera link de pagamento?",
            "links de pagamento que a empresa configurou previamente",
        ),
        (
            "Se eu cadastrar meus produtos, ele vai vender e gerar link de pagamento?",
            "Não cria links de cobrança",
        ),
        (
            "Oferece algum serviço de venda pelo WhatsApp, Facebook e Instagram?",
            "Messenger",
        ),
        (
            "Tá bom, eu vou fechar esse plano então. Manda o link para mim.",
            "contratação é assistida",
        ),
        ("Vou pagar no PIX", "não geramos cobrança"),
    ],
)
def test_pilot_verified_sales_intents_without_generating_checkout(
    question: str, expected: str
) -> None:
    answer = demo_verified_commercial_reply(question)
    assert answer is not None
    assert expected in answer
    assert len(answer) <= 850
    assert "Pix" not in answer  # explicit pilot sender rejects payment requests
    assert "pagamento confirmado" not in answer
    assert "assinatura ativada" not in answer
    approved_urls = re.findall(r"https?://[^\s]+", answer)
    assert all(url.rstrip(".,;!?") == DEMO_PAGE for url in approved_urls)


def test_pilot_sales_guard_is_narrow() -> None:
    assert demo_verified_commercial_reply("Qual é a cotação do dólar?") is None
    assert demo_verified_commercial_reply("Quanto custa o Escala?") is None
    assert demo_verified_commercial_reply("SAIR") is None
    assert MAX_PILOT_DEMO_REPLIES_PER_HOUR > MAX_BOT_REPLIES_PER_HOUR
    assert MAX_PILOT_DEMO_REPLIES_PER_HOUR <= 40


def test_pilot_continues_after_ten_messages_with_real_postgres_and_no_model(
    env: Env, db: Database
) -> None:
    env.sql("UPDATE messages SET handled=true WHERE direction='in'")
    tid = uuid.UUID(env.tenant("FM piloto consultivo", unique_email("pilot-sales-extended")))
    env.sql(
        "INSERT INTO tenant_settings (tenant_id, ai_enabled, ai_persona) "
        "VALUES (%s,false,'AnaClara')",
        (str(tid),),
    )
    with db.tx(tenant_id=tid) as conn:
        _, conversation_id = upsert_conversation(conn, tid, PHONE, "Contato de teste")
    settings = Settings.model_construct(
        pilot_greeting_enabled=True,
        pilot_greeting_tenant_id=str(tid),
        pilot_greeting_phone=PHONE,
        pilot_demo_enabled=True,
    )
    env.sql(
        "INSERT INTO messages (tenant_id,conversation_id,direction,author,body,status) "
        "SELECT %s,%s,'out','bot','Resposta anterior','sent' FROM generate_series(1,10)",
        (str(tid), conversation_id),
    )

    model = UnavailableAiModel()
    questions = [
        "Esse software faz venda? Ele gera link de pagamento?",
        "Se eu cadastrar meus produtos, ele vai vender e gerar link de pagamento?",
        "Esse software oferece venda pelo WhatsApp, Facebook e Instagram?",
        "Tá bom, vou fechar esse plano, manda o link para mim.",
        "Vou pagar no Pix.",
    ]
    for message in questions:
        env.sql(
            "INSERT INTO messages (tenant_id,conversation_id,direction,author,body,status,handled) "
            "VALUES (%s,%s,'in','customer',%s,'received',false)",
            (str(tid), conversation_id, message),
        )
        result = run_ai_replies(db, model, pilot_settings=settings)
        assert result.replied == 1 and result.handoffs == 0
        assert env.sql(
            "SELECT status, handoff_reason FROM conversations WHERE id=%s",
            (conversation_id,),
        ) == [("bot", None)]

    outgoing = env.sql(
        "SELECT body FROM messages WHERE conversation_id=%s AND direction='out' "
        "AND status='queued' ORDER BY created_at",
        (conversation_id,),
    )
    assert len(outgoing) == len(questions)
    assert "links de pagamento" in outgoing[0][0]
    assert "não gera" not in outgoing[0][0].lower() or "Não cria" in outgoing[0][0]
    assert "configuradas" in outgoing[1][0] or "configurou" in outgoing[1][0]
    assert "Messenger" in outgoing[2][0]
    assert DEMO_PAGE in outgoing[3][0]
    assert "cobrança" in outgoing[4][0]

    # Same governed transport: only authorized instance, tenant and contact.
    fake = FakeSendClient()
    sender = PilotDemoSender(cast(Any, fake), tenant_id=tid, phone=PHONE)
    cfg = {
        "__provider": "evolution",
        "instance_name": "fmav-" + hashlib.sha256(tid.bytes).hexdigest()[:32],
        "__demo_prices": "R$ 129,90|R$ 199,90|R$ 399,90",
        "__demo_quotas": "500|1.000|3.000|1000|3000",
    }
    for (message,) in outgoing:
        assert sender.send_text(cfg, PHONE, message) == "ABC123"
        with pytest.raises(SendError):
            sender.send_text(cfg, "5511977008899", message)
    assert len(fake.sent) == len(questions)

    # The extended pilot remains bounded; it is not an unlimited bypass.
    env.sql(
        "INSERT INTO messages (tenant_id,conversation_id,direction,author,body,status) "
        "SELECT %s,%s,'out','bot','Resposta de limite','sent' FROM generate_series(1,30)",
        (str(tid), conversation_id),
    )
    env.sql(
        "INSERT INTO messages (tenant_id,conversation_id,direction,author,body,status,handled) "
        "VALUES (%s,%s,'in','customer','Outra pergunta','received',false)",
        (str(tid), conversation_id),
    )
    blocked = run_ai_replies(db, model, pilot_settings=settings)
    assert blocked.handoffs == 1
    assert env.sql(
        "SELECT status,handoff_reason FROM conversations WHERE id=%s", (conversation_id,)
    ) == [("human", "limite_de_respostas")]
