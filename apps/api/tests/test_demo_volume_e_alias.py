import hashlib
import uuid
from typing import Any, cast

import pytest

from fm_seller.ai.seller import asked_for_volume, knowledge_reply
from fm_seller.channels.pilot_greeting import DEMO_VOLUME_QUESTION, PilotDemoSender
from fm_seller.recovery.senders import SendError

PHONE = "5511999990000"
TID = uuid.UUID("19d835d5-2d72-4e75-a3f3-0b1bc9330789")

FAQ = (
    ("O que é o AtendeVendeIA e o que ele faz?", "Resposta cadastrada sobre o produto."),
    ("Como funciona o atendimento?", "Resposta cadastrada sobre o atendimento."),
)


class _Client:
    def state(self, name: str) -> str:
        return "open"

    def _call(self, method: str, route: str, *, body: dict[str, Any]) -> dict[str, Any]:
        return {"key": {"id": "ABC123"}}


def _sender() -> PilotDemoSender:
    return PilotDemoSender(cast(Any, _Client()), tenant_id=TID, phone=PHONE)


def _cfg(echo: str = "") -> dict[str, str]:
    return {
        "__provider": "evolution",
        "instance_name": "fmav-" + hashlib.sha256(TID.bytes).hexdigest()[:32],
        "__demo_prices": "R$ 129,90|R$ 199,90|R$ 399,90",
        "__demo_quotas": "500|1.000|3.000|1000|3000",
        "__demo_echo": echo,
    }


@pytest.mark.parametrize(
    "question",
    ["O que ela faz?", "o que ela faz ?", "O que o sistema faz?", "O que ele faz"],
)
def test_product_pronouns_match_the_registered_question(question: str) -> None:
    assert knowledge_reply(question, FAQ) == FAQ[0][1]


def test_pronoun_alone_does_not_match_anything() -> None:
    assert knowledge_reply("ela", FAQ) is None
    assert knowledge_reply("o que é isso", FAQ) is None


def test_volume_answer_after_the_model_asked_in_its_own_words() -> None:
    assert asked_for_volume(DEMO_VOLUME_QUESTION)
    assert asked_for_volume(
        "Todos os planos têm as mesmas funcionalidades. A diferença é a franquia mensal: "
        "o Essencial tem 500 respostas de IA e o Escala tem 3.000 respostas por mês. "
        "Quantas respostas de IA você espera utilizar por mês?"
    )
    assert not asked_for_volume("Posso ajudar com mais alguma dúvida?")
    assert not asked_for_volume("")


def test_customer_number_may_be_echoed_but_other_numbers_may_not() -> None:
    sender = _sender()
    reply = (
        "Para o seu volume de 1.500 respostas por mês, o plano recomendado é o Escala, "
        "que conta com uma franquia mensal de 3.000 respostas de IA."
    )
    assert sender.send_text(_cfg("1500|1.500"), PHONE, reply) == "ABC123"
    with pytest.raises(SendError, match="número não autorizado"):
        sender.send_text(_cfg(""), PHONE, reply)
    with pytest.raises(SendError, match="número não autorizado"):
        sender.send_text(_cfg("1500|1.500"), PHONE, reply.replace("3.000", "7.777"))


PAY = "https://www.asaas.com/c/abc123"
OFFERS = [
    {
        "id": "av-essencial",
        "name": "Essencial",
        "price_cents": 12990,
        "quota": "500",
        "checkout_url": "https://www.asaas.com/c/ess",
    },
    {
        "id": "av-profissional",
        "name": "Profissional",
        "price_cents": 19990,
        "quota": "1000",
        "checkout_url": "https://www.asaas.com/c/pro",
    },
    {
        "id": "av-escala",
        "name": "Escala",
        "price_cents": 39990,
        "quota": "3000",
        "checkout_url": PAY,
    },
]


def test_named_plan_purchase_sends_registered_payment_link() -> None:
    from fm_seller.ai.seller import demo_close_sale_reply

    reply = demo_close_sale_reply("Quero contratar o Escala", "", OFFERS)
    assert reply is not None and PAY in reply and "R$ 399,90" in reply
    assert len(reply) <= 850


def test_yes_after_link_offer_uses_plan_from_previous_message() -> None:
    from fm_seller.ai.seller import demo_close_sale_reply

    prev = "O Escala custa R$ 399,90/mês. Posso te enviar o link de pagamento do Escala?"
    reply = demo_close_sale_reply("Sim", prev, OFFERS)
    assert reply is not None and PAY in reply
    assert demo_close_sale_reply("Sim", "Qualquer outra coisa", OFFERS) is None


def test_purchase_without_plan_asks_which_one() -> None:
    from fm_seller.ai.seller import demo_close_sale_reply

    reply = demo_close_sale_reply("manda o link de pagamento", "", OFFERS)
    assert reply is not None and "Qual plano" in reply and "http" not in reply


def test_guard_accepts_registered_payment_link_only() -> None:
    from fm_seller.ai.seller import demo_close_sale_reply

    sender = _sender()
    reply = demo_close_sale_reply("Quero contratar o Escala", "", OFFERS) or ""
    cfg = _cfg() | {"__demo_links": PAY}
    assert sender.send_text(cfg, PHONE, reply) == "ABC123"
    with pytest.raises(SendError, match="URL fora"):
        sender.send_text(_cfg(), PHONE, reply)
    with pytest.raises(SendError, match="URL fora"):
        sender.send_text(cfg, PHONE, reply.replace(PAY, "https://golpe.example/x"))


def test_guard_allows_pix_as_a_word_but_not_payment_instructions() -> None:
    sender = _sender()
    ok = "Retoma vendas paradas, como carrinho abandonado, Pix ou boleto sem pagamento."
    assert sender.send_text(_cfg(), PHONE, ok) == "ABC123"
    for bad in ("Use a chave Pix 123", "Pix copia e cola abaixo", "Pagamento confirmado!"):
        with pytest.raises(SendError):
            sender.send_text(_cfg(), PHONE, bad)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Meu volume de msg 1850", 1850),
        ("meu volume de mensagem 1850", 1850),
        ("Meu volume é 1.850", 1850),
        ("meu volume: 2 mil", 2000),
        ("o volume de respostas é 700", 700),
        ("Meu volume de msg", None),
        ("volume de 1850,50", None),
    ],
)
def test_volume_stated_without_per_month(text: str, expected: int | None) -> None:
    from fm_seller.channels.pilot_greeting import demo_requested_volume

    assert demo_requested_volume(text) == expected
