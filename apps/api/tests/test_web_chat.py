"""Canal "web": widget de chat embutido em site. Sem cookie; identidade por site+session_id,
IA real (aqui simulada) só com dados do cadastro do tenant, igual aos outros canais."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from fm_seller.ai.model import SimulatedAiModel
from fm_seller.api.app import create_app
from fm_seller.config import Settings
from fm_seller.db import Database
from tests.conftest import Env, unique_email

LINK = "https://pay.example.test/plano-site"
SITE = "fm"
ORIGIN = "https://fmtecnologiaia.com.br"
OTHER_ORIGIN = "https://nao-autorizado.example.test"


class WebChatSetup:
    def __init__(self, env: Env, db: Database) -> None:
        self.env, self.db = env, db
        self.tid = env.tenant("FM Site", unique_email("webchat"))
        self.tenant = uuid.UUID(self.tid)
        env.sql(
            "INSERT INTO tenant_settings (tenant_id, ai_enabled, ai_persona) "
            "VALUES (%s, true, 'Consultora')",
            (self.tid,),
        )
        env.sql(
            "INSERT INTO offers (tenant_id, name, description, price_cents, payment_url) "
            "VALUES (%s, 'Plano Site', 'Assinatura mensal', 9700, %s)",
            (self.tid, LINK),
        )

    def settings(self) -> Settings:
        base = self.env.settings()
        return base.model_copy(
            update={
                "web_chat_enabled": True,
                "web_chat_tenants": f"{SITE}:{self.tid}",
                "web_chat_origins": f"{ORIGIN},https://outronfcore.com.br",
            }
        )

    def client(self) -> TestClient:
        app = create_app(
            self.settings(), db=self.db, box=self.env.box, ai_model=SimulatedAiModel()
        )
        return TestClient(app)


@pytest.fixture
def s(env: Env, db: Database) -> WebChatSetup:
    return WebChatSetup(env, db)


def test_reply_comes_from_registered_offer_not_from_the_model(s: WebChatSetup) -> None:
    with s.client() as c:
        res = c.post(
            "/v1/web/chat",
            json={"site": SITE, "session_id": "sess-abc-1", "message": "Quanto custa o Plano Site?"},
            headers={"Origin": ORIGIN},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["handoff"] is False
    assert "R$ 97,00" in body["reply"]
    assert res.headers["access-control-allow-origin"] == ORIGIN


def test_same_session_continues_the_same_conversation(s: WebChatSetup) -> None:
    with s.client() as c:
        c.post(
            "/v1/web/chat",
            json={"site": SITE, "session_id": "sess-abc-2", "message": "oi"},
            headers={"Origin": ORIGIN},
        )
        c.post(
            "/v1/web/chat",
            json={"site": SITE, "session_id": "sess-abc-2", "message": "quero comprar"},
            headers={"Origin": ORIGIN},
        )
    rows = s.env.sql(
        "SELECT count(DISTINCT conversation_id) FROM messages m "
        "JOIN contact_channels cc ON true "
        "WHERE cc.tenant_id = %s AND cc.channel = 'web' AND cc.external_id = 'sess-abc-2' "
        "AND m.conversation_id IN (SELECT id FROM conversations WHERE tenant_id = %s)",
        (s.tid, s.tid),
    )
    assert rows[0][0] == 1


def test_disallowed_origin_gets_no_cors_header(s: WebChatSetup) -> None:
    with s.client() as c:
        res = c.post(
            "/v1/web/chat",
            json={"site": SITE, "session_id": "sess-abc-3", "message": "oi"},
            headers={"Origin": OTHER_ORIGIN},
        )
    # A chamada ainda é processada no servidor (defesa é o navegador bloqueando a leitura),
    # mas sem o cabeçalho a origem não autorizada não consegue ler a resposta.
    assert "access-control-allow-origin" not in res.headers


def test_unknown_site_is_not_found(s: WebChatSetup) -> None:
    with s.client() as c:
        res = c.post(
            "/v1/web/chat",
            json={"site": "nao-existe", "session_id": "sess-abc-4", "message": "oi"},
            headers={"Origin": ORIGIN},
        )
    assert res.status_code == 404


def test_disabled_by_default(env: Env, db: Database) -> None:
    app = create_app(env.settings(), db=db, box=env.box, ai_model=SimulatedAiModel())
    with TestClient(app) as c:
        res = c.post(
            "/v1/web/chat",
            json={"site": SITE, "session_id": "sess-abc-5", "message": "oi"},
            headers={"Origin": ORIGIN},
        )
    assert res.status_code == 404


def test_rejects_malformed_session_id(s: WebChatSetup) -> None:
    with s.client() as c:
        res = c.post(
            "/v1/web/chat",
            json={"site": SITE, "session_id": "não válido!!", "message": "oi"},
            headers={"Origin": ORIGIN},
        )
    assert res.status_code == 400


def test_preflight_reflects_allowed_origin_only(s: WebChatSetup) -> None:
    with s.client() as c:
        ok = c.options(
            "/v1/web/chat",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
        blocked = c.options(
            "/v1/web/chat",
            headers={
                "Origin": OTHER_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
    assert ok.headers.get("access-control-allow-origin") == ORIGIN
    assert "access-control-allow-origin" not in blocked.headers
