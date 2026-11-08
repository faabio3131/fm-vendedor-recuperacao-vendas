"""Photos: authorized tenant gallery, safe JPEG normalization and Evolution delivery."""

from __future__ import annotations

import base64
import io
import uuid
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from fm_seller.ai.model import UnavailableAiModel
from fm_seller.ai.seller import run_ai_replies
from fm_seller.channels.evolution_runtime import EvolutionSender, RoutedWhatsAppSender
from fm_seller.db import Database
from fm_seller.recovery.senders import SendError, SimulatedSender
from fm_seller.seller.photos import photo_reply
from tests.conftest import Env, login, unique_email
from tests.test_ai_seller import Setup


def image_png() -> bytes:
    image = Image.new("RGBA", (64, 64), (25, 100, 165, 100))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def offer(client: TestClient, *, name: str = "Bolsa Transversal Preta") -> str:
    result = client.post(
        "/v1/seller/offers",
        json={
            "name": name,
            "description": "Bolsa preta transversal com alça ajustável",
            "price_cents": 14990,
            "payment_url": "https://pay.example.test/bolsa",
        },
    )
    assert result.status_code == 200, result.text
    return str(result.json()["id"])


def test_photo_crud_private_blob_and_tenant_isolation(
    client: TestClient, env: Env, db: Database
) -> None:
    email = unique_email("photo-owner")
    first_tenant = env.tenant("Bolsas e Cia", email)
    assert login(client, email).status_code == 200
    offer_id = offer(client)
    path = f"/v1/seller/offers/{offer_id}/photos"
    created = client.post(path, content=image_png(), headers={"Content-Type": "image/png"})
    assert created.status_code == 200, created.text
    photo_id = created.json()["id"]
    listed = client.get("/v1/seller/offers").json()[0]
    assert listed["photos"][0]["id"] == photo_id
    assert listed["price_cents"] == 14990
    raw = client.get(f"{path}/{photo_id}")
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("image/jpeg")
    assert raw.headers["cache-control"] == "private, no-store"
    assert raw.content[:3] == bytes((255, 216, 255))
    with Image.open(io.BytesIO(raw.content)) as img:
        assert img.size == (64, 64)
        assert img.mode == "RGB"
        assert img.getexif() == {}

    with db.tx(tenant_id=uuid.UUID(first_tenant)) as conn:
        row = conn.execute("SELECT count(*) AS n FROM offer_photos").fetchone()
        assert row is not None and row["n"] == 1
    other_email = unique_email("photo-isolated")
    other_tenant = env.tenant("Outra loja", other_email)
    with db.tx(tenant_id=uuid.UUID(other_tenant)) as conn:
        row = conn.execute("SELECT count(*) AS n FROM offer_photos").fetchone()
        assert row is not None and row["n"] == 0
    client.post("/v1/auth/logout")
    assert login(client, other_email).status_code == 200
    assert client.get(f"{path}/{photo_id}").status_code == 404
    assert client.delete(f"{path}/{photo_id}").status_code == 404
    client.post("/v1/auth/logout")
    assert login(client, email).status_code == 200
    assert client.delete(f"{path}/{photo_id}").status_code == 200
    assert client.get(f"{path}/{photo_id}").status_code == 404
    assert env.sql("SELECT count(*) FROM offer_photos WHERE tenant_id=%s", (first_tenant,)) == [
        (0,)
    ]


def test_photo_validation_and_five_image_limit(client: TestClient, env: Env) -> None:
    email = unique_email("photos-limit")
    env.tenant("Loja de Bolsas", email)
    assert login(client, email).status_code == 200
    photo_path = f"/v1/seller/offers/{offer(client)}/photos"
    for raw, mime, expected_status in (
        (b"<svg>bad</svg>", "image/png", 400),
        (b"not an image", "image/jpeg", 400),
        (b"x" * 3_000_001, "image/png", 413),
        (image_png(), "text/html", 400),
    ):
        assert (
            client.post(photo_path, content=raw, headers={"Content-Type": mime}).status_code
            == expected_status
        )
    for _ in range(5):
        assert (
            client.post(
                photo_path, content=image_png(), headers={"Content-Type": "image/png"}
            ).status_code
            == 200
        )
    sixth = client.post(photo_path, content=image_png(), headers={"Content-Type": "image/png"})
    assert sixth.status_code == 400
    assert sixth.json()["error"]["code"] == "photo_limit"


def test_agent_cannot_upload_or_delete_photos(client: TestClient, env: Env) -> None:
    email = unique_email("photo-admin")
    tenant_id = env.tenant("Loja Fotos", email)
    assert login(client, email).status_code == 200
    oid = offer(client)
    photo_path = f"/v1/seller/offers/{oid}/photos"
    photo_id = client.post(
        photo_path, content=image_png(), headers={"Content-Type": "image/png"}
    ).json()["id"]
    agent = unique_email("photo-agent")
    env.invite(tenant_id, agent, "agent")
    client.post("/v1/auth/logout")
    assert login(client, agent).status_code == 200
    assert client.get(f"{photo_path}/{photo_id}").status_code == 200
    assert (
        client.post(
            photo_path, content=image_png(), headers={"Content-Type": "image/png"}
        ).status_code
        == 403
    )
    assert client.delete(f"{photo_path}/{photo_id}").status_code == 403


def test_bot_enqueues_real_photo_only_when_requested(env: Env, db: Database) -> None:
    s = Setup(env, db)
    row = env.sql("SELECT id FROM offers WHERE tenant_id=%s", (s.tid,))[0]
    from fm_seller.seller.photos import create_photo

    with db.tx(tenant_id=s.tenant) as conn:
        picture = create_photo(conn, s.tenant, row[0], image_png())
    s.say("Pode enviar uma foto do Curso X?")
    assert run_ai_replies(db, UnavailableAiModel()).replied == 1
    assert s.conv_state() == ("bot", None)
    queued = env.sql(
        "SELECT outbound_kind,outbound_photo_id,status,body FROM messages "
        "WHERE tenant_id=%s AND direction='out'",
        (s.tid,),
    )
    assert len(queued) == 1
    assert queued[0][0] == "offer_photo" and queued[0][1] == uuid.UUID(picture["id"])
    assert queued[0][2] == "queued"
    assert "Curso X" in queued[0][3] and "R$ 197,00" in queued[0][3]
    with db.tx(tenant_id=s.tenant) as conn:
        offers = conn.execute(
            "SELECT id,name,description,price_cents FROM offers WHERE tenant_id=%s", (s.tenant,)
        ).fetchall()
        result = photo_reply(conn, s.tenant, "Quero ver foto do Curso X", offers)
        assert result is not None and result[1] == uuid.UUID(picture["id"])
        assert photo_reply(conn, s.tenant, "Posso cadastrar fotos no sistema?", offers) is None


class FakeMediaClient:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = result if result is not None else {"key": {"id": "EVO_MEDIA_1"}}
        self.session = "open"

    def state(self, instance: str) -> str:
        assert instance == "fmav-" + "a" * 32
        return self.session

    def _call(self, method: str, route: str, *, body: dict[str, Any]) -> dict[str, Any]:
        assert method == "POST" and route.endswith("/message/sendMedia/fmav-" + "a" * 32)
        self.calls.append(body)
        return self.result


def test_evolution_send_media_private_base64_and_ack() -> None:
    fake = FakeMediaClient()
    sender = EvolutionSender(cast(Any, fake))
    route = RoutedWhatsAppSender(
        SimulatedSender(),
        sender,
        meta_enabled=False,
        evolution_enabled=True,
    )
    from fm_seller.seller.photos import normalize_image

    jpeg, _, _ = normalize_image(image_png())
    config = {"instance_name": "fmav-" + "a" * 32, "__provider": "evolution"}
    assert (
        route.send_image(config, "5511999991234", "Bolsa preta - R$ 149,90", jpeg) == "EVO_MEDIA_1"
    )
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["mediatype"] == "image" and call["mimetype"] == "image/jpeg"
    assert call["filename"] == "foto-produto.jpg"
    assert call["number"] == "5511999991234"
    assert base64.b64decode(call["media"]) == jpeg
    assert not call["media"].startswith("http")
    fake.result = {}
    with pytest.raises(RuntimeError):
        route.send_image(config, "5511999991234", "Produto", jpeg)
    assert len(fake.calls) == 2  # no retry after missing ACK
    fake.session = "close"
    with pytest.raises(SendError):
        route.send_image(config, "5511999991234", "Produto", jpeg)
    assert len(fake.calls) == 2
