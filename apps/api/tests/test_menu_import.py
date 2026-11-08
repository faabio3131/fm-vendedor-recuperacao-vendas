"""Menu upload: OCR contract, owner-reviewed publication, RBAC and tenant isolation."""

from __future__ import annotations

import base64
import io
import json
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
from pypdf import PdfWriter

from fm_seller.config import Settings
from fm_seller.errors import AppError
from fm_seller.seller.menu_import import (
    _parse_csv,
    _validated_media,
    cents_from_text,
    preview,
)
from tests.conftest import Env, login, unique_email

VALID_CSV = (
    "nome;categoria;descricao;preco\n"
    "Calabresa grande;Pizzas;Queijo e calabresa;R$ 49,90\n"
    "Mussarela broto;Pizzas;Queijo;29,00\n"
)


def png() -> bytes:
    image = Image.new("RGB", (80, 80), (190, 90, 50))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_prices_must_be_unambiguous_and_are_not_inferred() -> None:
    assert cents_from_text("R$ 49,90") == 4990
    assert cents_from_text("49.90") == 4990
    assert cents_from_text("1.000,00") == 100000
    assert cents_from_text("R$ 49,90 / R$ 79,90") is None
    assert cents_from_text("a combinar") is None
    assert cents_from_text("") is None
    assert cents_from_text("-10,00") is None


def test_csv_preview_categories_and_missing_price_not_published() -> None:
    result = _parse_csv(VALID_CSV.encode())
    assert len(result["items"]) == 2
    assert result["items"][0]["price_cents"] == 4990
    assert result["items"][0]["category"] == "Pizzas"
    missing = _parse_csv(b"nome;preco;categoria\nPizza meia calabresa;;Pizzas")
    assert missing["items"][0]["price_cents"] is None
    assert missing["items"][0]["needs_review"] is True


def test_file_validation_spoofing_and_pdf_bounds() -> None:
    with pytest.raises(AppError) as invalid:
        _validated_media(b"fake jpeg", "image/jpeg")
    assert invalid.value.status == 400
    with pytest.raises(AppError):
        _validated_media(b"%PDF-fake", "application/pdf")
    raw, mime = _validated_media(png(), "image/png")
    assert mime == "image/jpeg"
    assert raw.startswith(bytes((255, 216, 255)))

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    data = io.BytesIO()
    writer.write(data)
    content, detected = _validated_media(data.getvalue(), "application/pdf")
    assert detected == "application/pdf"
    assert content.startswith(b"%PDF-")


class VisionFake:
    payloads: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(body)
        return {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "items": [
                                            {
                                                "name": "Pizza Portuguesa",
                                                "category": "Pizzas",
                                                "description": "Presunto e ovos",
                                                "price": "54,90",
                                            },
                                            {
                                                "name": "Pizza Doce",
                                                "category": "Doces",
                                                "description": "",
                                                "price": "",
                                            },
                                        ]
                                    }
                                )
                            }
                        ]
                    },
                }
            ],
        }


def test_gemini_image_and_pdf_preview_are_private_and_reviewed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("fm_seller.seller.menu_import.GeminiModel", VisionFake)
    VisionFake.payloads.clear()
    settings = Settings(env="test", ai_api_key=SecretStr("dummy-key"))
    result = preview(png(), "image/png", settings)
    assert result["items"][0]["price_cents"] == 5490
    assert result["items"][1]["needs_review"] is True
    part = VisionFake.payloads[0]["contents"][0]["parts"][0]["inlineData"]
    assert part["mimeType"] == "image/jpeg"
    assert base64.b64decode(part["data"]).startswith(bytes((255, 216, 255)))
    assert VisionFake.payloads[0]["generationConfig"]["responseMimeType"] == "application/json"

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    data = io.BytesIO()
    writer.write(data)
    assert preview(data.getvalue(), "application/pdf", settings)["items"][0]["name"] == (
        "Pizza Portuguesa"
    )
    assert VisionFake.payloads[-1]["contents"][0]["parts"][0]["inlineData"]["mimeType"] == (
        "application/pdf"
    )


def _login_shop(client: TestClient, env: Env, name: str) -> str:
    email = unique_email("menu")
    tid = env.tenant(name, email)
    assert login(client, email).status_code == 200
    return tid


def test_csv_upload_review_publish_idempotent_and_tenant_separation(
    client: TestClient, env: Env
) -> None:
    _login_shop(client, env, "Pizzaria Menu")
    response = client.post(
        "/v1/seller/menu-import/preview",
        content=VALID_CSV.encode(),
        headers={"Content-Type": "text/csv"},
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 2

    bad = client.post(
        "/v1/seller/menu-import/commit",
        json={"items": items, "payment_url": "http://not-secure.test"},
    )
    assert bad.status_code == 400
    assert client.get("/v1/seller/offers").json() == []
    payload = {"items": items, "payment_url": "https://pedidos.example.test/cardapio"}
    created = client.post("/v1/seller/menu-import/commit", json=payload)
    assert created.status_code == 200, created.text
    assert created.json() == {"created": 2, "duplicates": 0}
    repeat = client.post("/v1/seller/menu-import/commit", json=payload)
    assert repeat.json() == {"created": 0, "duplicates": 2}
    listed = client.get("/v1/seller/offers").json()
    assert len(listed) == 2
    assert all(x["active"] and x["category"] == "Pizzas" for x in listed)
    assert {x["price_cents"] for x in listed} == {4990, 2900}
    client.post("/v1/auth/logout")
    _login_shop(client, env, "Outra Pizzaria")
    assert client.get("/v1/seller/offers").json() == []


def test_missing_price_is_rejected_on_commit(client: TestClient, env: Env) -> None:
    _login_shop(client, env, "Menu Incompleto")
    item = _parse_csv(b"nome;preco\nPizza Broto;")["items"][0]
    result = client.post(
        "/v1/seller/menu-import/commit",
        json={"items": [item], "payment_url": "https://pedidos.example.test"},
    )
    assert result.status_code == 422
    assert client.get("/v1/seller/offers").json() == []


def test_agent_cannot_preview_or_publish_menu(client: TestClient, env: Env) -> None:
    email = unique_email("menu-owner")
    tid = env.tenant("Menu Proprietário", email)
    agent = unique_email("menu-agent")
    env.invite(tid, agent, "agent")
    assert login(client, agent).status_code == 200
    preview_res = client.post(
        "/v1/seller/menu-import/preview",
        content=VALID_CSV.encode(),
        headers={"Content-Type": "text/csv"},
    )
    assert preview_res.status_code == 403
    publish_res = client.post(
        "/v1/seller/menu-import/commit",
        json={
            "items": [{"name": "Pizza", "price_cents": 5000}],
            "payment_url": "https://pedidos.example.test",
        },
    )
    assert publish_res.status_code == 403


def test_binary_upload_obeys_special_cap_and_never_falls_back_to_fake_vision(
    client: TestClient, env: Env
) -> None:
    _login_shop(client, env, "Menu Foto")
    oversized = client.post(
        "/v1/seller/menu-import/preview",
        content=b"x" * 8_000_001,
        headers={"Content-Type": "application/pdf"},
    )
    assert oversized.status_code == 413
    malformed = client.post(
        "/v1/seller/menu-import/preview",
        content=b"not a PDF",
        headers={"Content-Type": "application/pdf"},
    )
    assert malformed.status_code == 400
