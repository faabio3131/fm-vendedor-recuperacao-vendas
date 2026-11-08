"""Sales conversation quality from the November 8 Cakto abandonment journey."""

from __future__ import annotations

import uuid

import pytest

from fm_seller.ai.gemini import build_system_prompt
from fm_seller.ai.model import AiContext, AiReply, OfferView, SimulatedAiModel
from fm_seller.ai.sales_quality import (
    fm_consultative_reply,
    is_verified_fm_catalog,
    needs_contextual_answer,
)
from fm_seller.ai.seller import run_ai_replies
from fm_seller.config import Settings
from fm_seller.db import Database
from tests.conftest import Env
from tests.test_ai_seller import Setup

FM_OFFERS = [
    {"name": "AtendeVendeIA Essencial", "price_cents": 12990},
    {"name": "AtendeVendeIA Profissional", "price_cents": 19990},
    {"name": "AtendeVendeIA Escala", "price_cents": 39990},
]
TENANT = uuid.UUID("db09b1a0-49e7-408f-8bca-ef6727a9ba31")


@pytest.mark.parametrize(
    ("question", "should_contain"),
    [
        ("O que ela faz de verdade?", "Na prática, imagine alguém"),
        ("Como funciona na prática?", "canal conectado"),
        ("Para que serve o AtendeVendeIA?", "ofertas que você cadastrou"),
        ("Fiquei com dúvidas por isso não efetuei o pagamento", "sem fazer o pagamento agora"),
        ("O sistema recupera vendas?", "acompanhamento pelo WhatsApp"),
        ("Como funciona o pagamento do plano?", "não gera cobranças"),
        ("Aceita Pix?", "plataforma de cobrança"),
        ("Vale a pena usar?", "não existe promessa de vendas garantidas"),
        ("Como funciona esse relatório e onde vejo?", "menu Relatórios"),
        (
            "Eu vendo bolsas de diversas marcas e modelos, o que ela faria pela minha empresa?",
            "Na sua loja de bolsas",
        ),
        ("Trabalho com roupas e sapatos, como ajudaria minha loja?", "Na sua loja"),
    ],
)
def test_fm_answers_real_commercial_questions_without_unverified_promises(
    question: str, should_contain: str
) -> None:
    reply = fm_consultative_reply(question, FM_OFFERS, tenant_id=TENANT, fm_tenant_id=str(TENANT))
    assert reply is not None
    assert should_contain in reply
    assert reply.count("?") <= 1
    assert "https://" not in reply and "R$" not in reply
    assert "garantia de vendas" not in reply
    assert len(reply) <= 800


@pytest.mark.parametrize(
    "question",
    [
        "Quanto custa o Essencial?",
        "Me envie o link do Escala",
        "Posso pedir cancelamento?",
        "Quero atendimento humano",
        "Qual é a previsão do tempo?",
    ],
)
def test_commercial_facts_do_not_override_buying_or_handoff(
    question: str,
) -> None:
    assert (
        fm_consultative_reply(question, FM_OFFERS, tenant_id=TENANT, fm_tenant_id=str(TENANT))
        is None
    )


def test_scope_only_approved_fm_tenant_and_real_catalogue() -> None:
    assert is_verified_fm_catalog(FM_OFFERS)
    assert not is_verified_fm_catalog([{"name": "Curso X"}])
    assert (
        fm_consultative_reply(
            "O que ela faz de verdade?",
            FM_OFFERS,
            tenant_id=uuid.uuid4(),
            fm_tenant_id=str(TENANT),
        )
        is None
    )
    assert (
        fm_consultative_reply(
            "O que ela faz de verdade?",
            [{"name": "Curso X"}],
            tenant_id=TENANT,
            fm_tenant_id=str(TENANT),
        )
        is None
    )


def test_context_sensitive_objections_go_to_model_not_stale_faq() -> None:
    assert needs_contextual_answer("O que ela faz de verdade?")
    assert needs_contextual_answer("Fiquei com dúvidas, não paguei")
    assert not needs_contextual_answer("Qual a garantia do Curso X?")


def test_model_prompt_teaches_specific_objection_handling_and_verified_capabilities() -> None:
    prompt = build_system_prompt(
        AiContext(
            "",
            "Ana",
            (OfferView("o1", "Curso X", "Aprenda X", "R$ 197,00"),),
            (("customer", "O que o produto faz de verdade?"),),
            knowledge=(("O que o produto faz?", "Permite assistir às aulas."),),
        )
    )
    assert "QUALIDADE DO ATENDIMENTO CONSULTIVO" in prompt
    assert "não o pressione" in prompt
    assert "recuperar conversa NÃO prova recuperação financeira" in prompt
    assert "BASE DE CONHECIMENTO" in prompt
    assert "Não diga que um Pix ficou sem pagamento" in prompt
    assert "personalize a explicação" in prompt
    assert "localização de recursos do painel" in prompt


def test_real_fm_commercial_flow_uses_safe_explanation_without_model(
    env: Env, db: Database
) -> None:
    s = Setup(env, db)
    env.sql("DELETE FROM offers WHERE tenant_id = %s", (s.tid,))
    for offer in FM_OFFERS:
        env.sql(
            "INSERT INTO offers (tenant_id,name,description,price_cents,payment_url) "
            "VALUES (%s,%s,'Atendimento WhatsApp e links cadastrados',%s,%s)",
            (
                s.tid,
                offer["name"],
                offer["price_cents"],
                "https://pay.example.test/atendevendeia",
            ),
        )
    env.sql(
        "INSERT INTO knowledge_items (tenant_id,question,answer) VALUES (%s,%s,%s)",
        (
            s.tid,
            "O que ela faz de verdade?",
            "Faz Pix, boleto e qualquer cobrança automaticamente.",
        ),
    )
    settings = Settings.model_construct(
        pilot_greeting_enabled=False,
        pilot_demo_enabled=False,
        pilot_greeting_tenant_id=s.tid,
    )
    model = SimulatedAiModel()
    s.say("Fiquei com dúvidas por isso não efetuei o pagamento")
    assert run_ai_replies(db, model, pilot_settings=settings).replied == 1
    assert "sem fazer o pagamento agora" in s.outbound()[-1][1]
    s.say("O que ela faz de verdade?")
    assert run_ai_replies(db, model, pilot_settings=settings).replied == 1
    response = s.outbound()[-1][1]
    assert "Na prática" in response and "não processa pagamentos" in response
    assert "Pix, boleto" not in response
    assert model.calls == []
    assert s.conv_state() == ("bot", None)


def test_other_subscriber_uses_model_for_contextual_question(env: Env, db: Database) -> None:
    s = Setup(env, db)
    env.sql(
        "INSERT INTO knowledge_items (tenant_id,question,answer) VALUES (%s,%s,%s)",
        (s.tid, "O que ela faz de verdade?", "Resposta genérica sem contexto."),
    )
    s.say("O que ela faz de verdade?")
    model = SimulatedAiModel(
        forced=AiReply(
            "O Curso X oferece os recursos descritos pela loja. "
            "Você quer entender primeiro como funciona o conteúdo?"
        )
    )
    settings = Settings.model_construct(
        pilot_greeting_enabled=False,
        pilot_demo_enabled=False,
        pilot_greeting_tenant_id=str(uuid.uuid4()),
    )
    assert run_ai_replies(db, model, pilot_settings=settings).replied == 1
    assert len(model.calls) == 1
    assert "Resposta genérica" not in s.outbound()[-1][1]


@pytest.mark.parametrize(
    ("customer", "expected"),
    [
        ("Como funciona esse relatório e onde vejo?", "menu Relatórios"),
        (
            "Eu vendo bolsas de diversas marcas e modelos, o que ela faria pela minha empresa?",
            "Na sua loja de bolsas",
        ),
    ],
)
def test_actual_retail_and_reports_dialogue_uses_grounded_fm_answer(
    env: Env, db: Database, customer: str, expected: str
) -> None:
    s = Setup(env, db)
    env.sql("DELETE FROM offers WHERE tenant_id = %s", (s.tid,))
    for offer in FM_OFFERS:
        env.sql(
            "INSERT INTO offers (tenant_id,name,description,price_cents,payment_url) "
            "VALUES (%s,%s,'WhatsApp com ofertas cadastradas',%s,%s)",
            (
                s.tid,
                offer["name"],
                offer["price_cents"],
                "https://pay.example.test/atendevendeia",
            ),
        )
    env.sql(
        "INSERT INTO knowledge_items (tenant_id,question,answer) VALUES (%s,%s,%s)",
        (s.tid, customer, "Não conheço esse detalhe nem como o relatório funciona."),
    )
    model = SimulatedAiModel()
    settings = Settings.model_construct(
        pilot_greeting_enabled=False,
        pilot_demo_enabled=False,
        pilot_greeting_tenant_id=s.tid,
    )
    s.say(customer)
    assert run_ai_replies(db, model, pilot_settings=settings).replied == 1
    response = s.outbound()[-1][1]
    assert expected in response
    assert "Não conheço esse detalhe" not in response
    assert "Pix sem pagamento" not in response
    assert "https://" not in response
    assert len(response) <= 800
    assert model.calls == []
    assert s.conv_state() == ("bot", None)


def test_reports_and_retail_answer_never_leaks_to_another_tenant() -> None:
    for question in (
        "Como funciona esse relatório e onde vejo?",
        "Eu vendo bolsas de diversas marcas, como me ajudaria?",
    ):
        assert (
            fm_consultative_reply(
                question,
                FM_OFFERS,
                tenant_id=uuid.uuid4(),
                fm_tenant_id=str(TENANT),
            )
            is None
        )


def test_reports_message_is_specific_and_not_a_financial_confirmation() -> None:
    answer = fm_consultative_reply(
        "Como funciona esse relatório e onde vejo?",
        FM_OFFERS,
        tenant_id=TENANT,
        fm_tenant_id=str(TENANT),
    )
    assert answer is not None
    for term in ("período", "entregues", "lidas", "responderam", "CSV", "produto"):
        assert term in answer
    assert "Uma resposta no WhatsApp não é, sozinha, uma venda paga" in answer
