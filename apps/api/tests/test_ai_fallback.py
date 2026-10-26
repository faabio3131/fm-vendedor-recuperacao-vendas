from fm_seller.ai.model import AiContext, AiReply, FallbackAiModel


class _Boom:
    available = True

    def reply(self, ctx):
        raise RuntimeError("503")


class _Ok:
    available = True
    model = "reserva"

    def reply(self, ctx):
        return AiReply("oi")


CTX = AiContext(persona="", customer_name="", offers=(), history=(("customer", "oi"),))


def test_usa_reserva_quando_principal_falha():
    assert FallbackAiModel(_Boom(), _Ok()).reply(CTX).text == "oi"


def test_principal_ok_nao_chama_reserva():
    assert FallbackAiModel(_Ok(), _Boom()).reply(CTX).text == "oi"


def test_falha_das_duas_propaga_para_o_repasse():
    import pytest

    with pytest.raises(RuntimeError):
        FallbackAiModel(_Boom(), _Boom()).reply(CTX)
