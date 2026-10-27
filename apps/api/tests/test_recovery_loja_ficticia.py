"""Recuperação de vendas na loja fictícia (cliente novo, sem configuração da FM).

Percorre a jornada completa com os textos padrão da plataforma e confere o que cada cliente
final receberia: sequência, horário de silêncio, compra no meio, pedido de parar e dedupe.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from fm_seller.events import normalize as n
from fm_seller.events.normalize import CheckoutEvent
from fm_seller.recovery.optout import is_opt_out, suppress_identity
from tests.test_recovery_engine import Ctx, ctx  # noqa: F401  (fixture)

SEG_11H = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)  # segunda, 11h em São Paulo
LINK = "https://pay.example.test/kit-suculentas"
MARIA, JOAO, CARLA = "5511911110001", "5511911110002", "5511911110003"


def evento(kind: str, ref: str, nome: str, fone: str, cents: int = 8990) -> CheckoutEvent:
    return CheckoutEvent(
        kind=kind,
        source_event="x",
        external_ref=ref,
        name=nome,
        email=None,
        phone=fone,
        product_id="kit",
        product_name="Kit Suculentas",
        amount_cents=cents,
        payment_url=LINK,
    )


def test_jornada_completa_da_loja_ficticia(ctx: Ctx) -> None:  # noqa: F811
    # Maria abandona o carrinho, recebe os dois primeiros lembretes e compra depois do segundo.
    ctx.handle(evento(n.ABANDONED_CART, "maria", "maria lima", MARIA), SEG_11H)
    # João gera um Pix e pede para parar depois do primeiro lembrete.
    ctx.handle(evento(n.PIX_PENDING, "joao", "joao pereira", JOAO), SEG_11H)
    # Carla abandona o carrinho às 22h (silêncio): nada pode sair antes das 8h.
    noite = SEG_11H.replace(hour=1) + timedelta(days=1)  # 22h de segunda em São Paulo
    ctx.handle(evento(n.ABANDONED_CART, "carla", "carla dias", CARLA), noite)

    ctx.run(SEG_11H + timedelta(minutes=16))
    por_fone = {m.to_phone: m for m in ctx.sender.sent}
    assert JOAO in por_fone and MARIA not in por_fone  # Pix sai em 15 min; carrinho em 30

    ctx.run(SEG_11H + timedelta(minutes=31))
    textos = {m.to_phone: m.body for m in ctx.sender.sent}
    assert "Oi, Maria!" in textos[MARIA] and LINK in textos[MARIA]
    assert "Kit Suculentas" in textos[MARIA]
    assert "R$ 89,90" in textos[JOAO]

    # João responde "parar": nada mais é enviado para ele.
    assert is_opt_out("Parar por favor")
    with ctx.db.tx(tenant_id=ctx.tid) as conn:
        suppress_identity(conn, ctx.tid, JOAO, "opt_out", SEG_11H + timedelta(minutes=40))
    # Segundo lembrete de Maria (24 h) sai; Carla só tem passos a partir das 8h.
    ctx.run(SEG_11H + timedelta(days=1, minutes=1))
    enviados = [(m.to_phone, m.body) for m in ctx.sender.sent]
    assert sum(1 for p, _ in enviados if p == JOAO) == 1
    assert sum(1 for p, _ in enviados if p == MARIA) == 2
    carla_steps = ctx.steps("carla")
    assert carla_steps[0]["scheduled_at"].astimezone(UTC).hour >= 11  # 8h SP = 11h UTC

    # Maria compra: caso vira "recuperado" com o valor, e o 3º lembrete é cancelado.
    ctx.handle(
        evento(n.PURCHASE_APPROVED, "pag-maria", "maria lima", MARIA),
        SEG_11H + timedelta(days=1, hours=2),
    )
    caso = ctx.case("maria")
    assert caso["status"] == "recovered" and caso["recovered_amount_cents"] == 8990
    ctx.run(SEG_11H + timedelta(days=4))
    assert sum(1 for m in ctx.sender.sent if m.to_phone == MARIA) == 2
    assert ctx.case("joao")["closed_reason"] == "opt_out"

    # Nenhuma variável ficou sem preencher nem texto sem nome em nenhuma mensagem enviada.
    for m in ctx.sender.sent:
        assert not re.search(r"\{[a-z_]+\}", m.body), m.body
