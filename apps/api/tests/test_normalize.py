"""Normalizadores. Os payloads abaixo são SINTÉTICOS (formato a confirmar com evento real)."""

from __future__ import annotations

from fm_seller.events import normalize as n


def test_phone_brazil_variants() -> None:
    assert n.normalize_phone_br("(11) 99999-8888") == "5511999998888"
    assert n.normalize_phone_br("+55 11 99999-8888") == "5511999998888"
    assert n.normalize_phone_br("5511999998888") == "5511999998888"
    assert n.normalize_phone_br("1133334444") == "551133334444"
    assert n.normalize_phone_br("123") is None
    assert n.normalize_phone_br(None) is None
    assert n.normalize_phone_br({"x": 1}) is None


def test_to_cents() -> None:
    assert n.to_cents("197.00") == 19700
    assert n.to_cents("197,5") == 19750
    assert n.to_cents(97) == 9700
    assert n.to_cents(None) == 0
    assert n.to_cents("abc") == 0
    assert n.to_cents(-5) == 0


def test_cakto_abandoned_cart() -> None:
    ev = n.normalize_cakto(
        {
            "event": "checkout_abandonment",
            "data": {
                "id": "ck-1",
                "customer": {"name": "Ana", "email": "ANA@Example.test", "phone": "11999998888"},
                "product": {"id": "p1", "name": "Curso"},
                "amount": 197,
                "checkoutUrl": "https://pay.example.test/x",
            },
        }
    )
    assert ev is not None
    assert ev.kind == n.ABANDONED_CART
    assert (ev.email, ev.phone, ev.external_ref) == ("ana@example.test", "5511999998888", "ck-1")
    assert (ev.product_id, ev.amount_cents) == ("p1", 19700)
    assert ev.payment_url == "https://pay.example.test/x"


def test_hotmart_events_and_fallback_ref() -> None:
    ev = n.normalize_hotmart(
        {
            "event": "PURCHASE_OUT_OF_SHOPPING_CART",
            "data": {
                "buyer": {
                    "name": "Bia",
                    "email": "bia@example.test",
                    "checkout_phone": "21988887777",
                },
                "product": {"id": 77, "name": "Mentoria"},
            },
        }
    )
    assert ev is not None
    assert ev.kind == n.ABANDONED_CART
    assert ev.external_ref.startswith("auto-")  # sem transação: referência derivada e estável
    again = n.normalize_hotmart(
        {
            "event": "PURCHASE_OUT_OF_SHOPPING_CART",
            "data": {
                "buyer": {"email": "bia@example.test", "checkout_phone": "21988887777"},
                "product": {"id": 77},
            },
        }
    )
    assert again is not None and again.external_ref == ev.external_ref


def test_unknown_event_is_ignored_and_javascript_url_dropped() -> None:
    assert n.normalize_cakto({"event": "algo_novo"}) is None
    assert n.normalize_hotmart({}) is None
    ev = n.normalize_cakto(
        {"event": "boleto_gerado", "data": {"id": "b1", "boletoUrl": "javascript:alert(1)"}}
    )
    assert ev is not None and ev.payment_url is None
