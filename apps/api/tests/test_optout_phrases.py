"""Pedido de não contato: frases longas (áudio transcrito) e formas comuns, sem falso alarme."""

from __future__ import annotations

import pytest

from fm_seller.recovery.optout import is_opt_out


@pytest.mark.parametrize(
    "text",
    [
        # o caso do áudio de teste (67 caracteres, antes ignorado)
        "Por favor, pode parar de me mandar mensagem? Não quero mais receber.",
        "para de me mandar mensagem",
        "Pare de enviar mensagem pra mim, por favor",
        "me tira da lista",
        "Por favor me tire da lista de vocês, já falei que não tenho interesse no curso.",
        "pode remover meu número da lista",
        "Não quero mais receber mensagens de vocês, obrigado pela atenção até aqui.",
        "não me mande mais nada, eu já comprei em outro lugar e não preciso do curso",
        "quero sair da lista",
        "Quero me descadastrar, por favor, já faz tempo que recebo isso e não quero mais.",
        "NÃO ME LIGUE MAIS",
    ],
)
def test_clear_stop_requests_are_recognized(text: str) -> None:
    assert is_opt_out(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Oi, boa tarde. Eu queria saber quanto custa o curso e se dá para pagar no Pix. Obrigado.",
        "Pode me mandar o link de pagamento? Vou pagar hoje ainda.",
        "Quero parar de pagar boleto e passar para o Pix, como faço para trocar a forma?",
        "Vocês param de enviar o material depois do primeiro mês ou continua chegando todo mês?",
        "Não quero receber o link por e-mail, prefiro aqui mesmo no WhatsApp.",
        "Para de manhã ou à tarde, qual horário o suporte atende melhor para mim?",
        "Preciso sair para almoçar, volto em uma hora e já te respondo sobre o curso, tudo bem?",
        "Quero cancelar o pedido do curso de ontem porque paguei duas vezes, pode me ajudar?",
        "",
    ],
)
def test_normal_conversation_is_not_a_stop_request(text: str) -> None:
    assert is_opt_out(text) is False


def test_very_long_text_is_never_treated_as_stop() -> None:
    assert is_opt_out("me tira da lista " + "blá " * 200) is False


def test_old_short_behavior_is_kept() -> None:
    for t in ("sair", "SAIR", "Parar", "pare", "stop", "cancelar", "descadastrar"):
        assert is_opt_out(t) is True
