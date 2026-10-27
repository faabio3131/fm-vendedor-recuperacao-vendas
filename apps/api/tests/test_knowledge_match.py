from fm_seller.ai.gemini import build_system_prompt
from fm_seller.ai.model import AiContext, OfferView
from fm_seller.ai.seller import knowledge_reply

ITEMS = (
    ("Como funciona a entrega do curso?", "Acesso liberado por e-mail na hora."),
    ("Qual a garantia do curso?", "Sete dias de garantia."),
)


def test_match_requires_most_of_the_stored_question() -> None:
    assert knowledge_reply("como funciona a entrega do curso", ITEMS) == ITEMS[0][1]
    assert knowledge_reply("qual é a garantia do curso?", ITEMS) == ITEMS[1][1]


def test_vague_or_unrelated_text_falls_through_to_the_model() -> None:
    assert knowledge_reply("curso", ITEMS) is None
    assert knowledge_reply("vocês aceitam cartão de crédito parcelado?", ITEMS) is None
    assert knowledge_reply("entrega", ITEMS) is None


def test_prompt_carries_the_knowledge_and_says_not_to_hand_off_for_it() -> None:
    ctx = AiContext(
        "",
        "Ana",
        (OfferView("1", "Curso X", "Aprenda", "R$ 197,00"),),
        (("customer", "oi"),),
        knowledge=ITEMS,
    )
    prompt = build_system_prompt(ctx)
    assert "BASE DE CONHECIMENTO" in prompt and "Sete dias de garantia." in prompt
    assert "NÃO transfira" in prompt
