"""Unit tests for catalog-grounded photo selection without external services."""
from __future__ import annotations

import uuid
from typing import Any, cast

from fm_seller.db import Conn
from fm_seller.seller.photos import photo_reply, request_for_photo


class FakeCatalog:
    def __init__(self) -> None:
        self.first_offer = uuid.UUID("00000000-0000-0000-0000-000000000011")
        self.second_offer = uuid.UUID("00000000-0000-0000-0000-000000000022")
        self.first_image = uuid.UUID("00000000-0000-0000-0000-000000000033")
        self.second_image = uuid.UUID("00000000-0000-0000-0000-000000000044")

    def execute(self, sql: str, params: tuple[Any, ...]) -> FakeCatalog:
        assert "JOIN offers" in sql
        assert len(params) == 1
        return self

    def fetchall(self) -> list[dict[str, Any]]:
        return [
            {"offer_id": self.first_offer, "id": self.first_image,
             "name": "Bolsa Transversal Preta", "brand": "Bella",
             "description": "Alça ajustável, tecido leve", "price_cents": 14990},
            {"offer_id": self.second_offer, "id": self.second_image,
             "name": "Bolsa Tote Marrom", "brand": "Moda",
             "description": "Bolsa espaçosa", "price_cents": 19900},
        ]


def answer(
    conn: FakeCatalog, question: str, last: str | None = None
) -> tuple[str, uuid.UUID | None] | None:
    return photo_reply(
        cast(Conn, cast(Any, conn)),
        uuid.UUID("00000000-0000-0000-0000-000000000099"),
        question, [], last_bot_text=last,
    )


def test_explicit_request_selects_real_photo_price_brand_and_details() -> None:
    db = FakeCatalog()
    result = answer(db, "Por favor me envie a foto da bolsa transversal preta")
    assert result is not None
    assert result[1] == db.first_image
    assert "Bella Bolsa Transversal Preta" in result[0]
    assert "R$ 149,90" in result[0]
    assert "Alça ajustável" in result[0]
    assert "link de compra" in result[0]


def test_contextual_request_sends_only_when_unambiguous() -> None:
    db = FakeCatalog()
    selected = answer(
        db, "Quero ver esse modelo",
        "Temos Bolsa Transversal Preta por R$ 149,90.",
    )
    assert selected is not None and selected[1] == db.first_image

    ambiguous = answer(
        db, "Quero ver esse modelo",
        "Temos Bolsa Transversal Preta e Bolsa Tote Marrom.",
    )
    assert ambiguous is not None and ambiguous[1] is None
    assert "Qual modelo" in ambiguous[0]

    no_context = answer(db, "Quero ver esse modelo")
    assert no_context is not None and no_context[1] is None


def test_unknown_product_never_substitutes_another_photo() -> None:
    db = FakeCatalog()
    response = answer(db, "Quero ver foto da mochila vermelha")
    assert response is not None and response[1] is None
    assert "Não encontrei" in response[0]


def test_availability_question_not_mistaken_for_photo_request() -> None:
    assert not request_for_photo("Você tem bolsa transversal preta?")
    assert not request_for_photo("Quero ver os planos")
    assert request_for_photo("Tem uma foto da bolsa preta?")
    assert request_for_photo("Me mostra essa bolsa")
    assert request_for_photo("Quero ver esse modelo")
