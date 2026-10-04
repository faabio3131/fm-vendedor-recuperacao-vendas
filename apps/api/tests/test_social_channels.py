"""Messenger e Instagram. Payloads seguem a documentação da Meta, mas sintéticos (sem conta real).

Nada aqui prova a integração real: ver docs/PENDENCIAS_EXTERNAS.md.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from fm_seller.ai.model import SimulatedAiModel
from fm_seller.ai.seller import run_ai_replies
from fm_seller.channels.meta_api import MetaClient
from fm_seller.channels.outbox import flush_outbox
from fm_seller.channels.social import BY_CHANNEL, SOCIAL
from fm_seller.channels.social_send import SocialSender, build_social_senders
from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.providers.catalog import get_provider
from fm_seller.providers.testers import MetaConnectionTester
from fm_seller.recovery.cold import detect_cold_conversations
from fm_seller.recovery.senders import SendError, SimulatedSender, UnavailableSender
from tests.conftest import Env, login, unique_email
from tests.test_whatsapp_real import Fake

APP_SECRET = "app-secret-de-teste"
PAGE = "104500000001"
IG = "178900000002"
PSID = "2501000000000011"
IGSID = "9901000000000022"
TOKEN = "EAAGsegredo-social-777"

CFG = {
    "messenger": {
        "page_id": PAGE,
        "page_access_token": TOKEN,
        "app_secret": APP_SECRET,
    },
    "instagram_dm": {
        "instagram_account_id": IG,
        "access_token": TOKEN,
        "app_secret": APP_SECRET,
    },
}
OWN = {"messenger": PAGE, "instagram_dm": IG}
SENDER = {"messenger": PSID, "instagram_dm": IGSID}
CHANNEL = {"messenger": "messenger", "instagram_dm": "instagram"}
BOTH = ["messenger", "instagram_dm"]


def sign(raw: bytes, secret: str = APP_SECRET) -> dict[str, str]:
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={digest}", "Content-Type": "application/json"}


def payload(provider: str, events: list[dict[str, Any]], entry_id: str | None = None) -> bytes:
    obj = "page" if provider == "messenger" else "instagram"
    entry = {"id": entry_id or OWN[provider], "time": 1, "messaging": events}
    return json.dumps({"object": obj, "entry": [entry]}).encode()


def incoming(provider: str, mid: str, body: str) -> dict[str, Any]:
    return {
        "sender": {"id": SENDER[provider]},
        "recipient": {"id": OWN[provider]},
        "timestamp": 1,
        "message": {"mid": mid, "text": body},
    }


class Social:
    def __init__(self, client: TestClient, env: Env, provider: str) -> None:
        env.sql(
            "UPDATE messages SET status = 'failed' WHERE direction = 'out' AND status = 'queued'"
        )
        self.c, self.env, self.provider = client, env, provider
        email = unique_email("soc")
        self.tid = env.tenant("Loja Social", email)
        assert login(client, email).status_code == 200
        res = client.put(f"/v1/connections/{provider}", json={"values": CFG[provider]})
        assert res.status_code == 200, res.text
        self.pid = res.json()["webhook_url"].rsplit("/", 1)[1]
        self.verify_token = res.json()["webhook_secret_once"]

    def url(self) -> str:
        return f"/v1/webhooks/{self.provider}/{self.pid}"

    def post(self, raw: bytes, headers: dict[str, str] | None = None) -> Any:
        return self.c.post(self.url(), content=raw, headers=headers or sign(raw))

    def send_in(self, mid: str, body: str) -> Any:
        return self.post(payload(self.provider, [incoming(self.provider, mid, body)]))

    def conv(self) -> Any:
        return self.env.sql(
            "SELECT id, channel, status FROM conversations WHERE tenant_id = %s", (self.tid,)
        )[0]

    def cols(self, cols: str, body: str) -> list[tuple[Any, ...]]:
        """Colunas da mensagem com este texto, só deste cliente (a tabela é compartilhada)."""
        return self.env.sql(
            f"SELECT {cols} FROM messages WHERE tenant_id = %s AND body = %s", (self.tid, body)
        )

    def queue(self, body: str, conv: Any = None, author: str = "bot") -> None:
        self.env.sql(
            "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status) "
            "VALUES (%s, %s, 'out', %s, %s, 'queued')",
            (self.tid, conv or self.conv()[0], author, body),
        )


@pytest.fixture(params=BOTH)
def soc(request: pytest.FixtureRequest, client: TestClient, env: Env) -> Social:
    return Social(client, env, request.param)


def both_senders(sim: SimulatedSender) -> dict[str, Any]:
    return {"messenger": sim, "instagram": sim}


# ------------------------------------------------------------------ entrada


def test_handshake_requires_the_generated_token(soc: Social) -> None:
    ok = soc.c.get(
        soc.url(),
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": soc.verify_token,
            "hub.challenge": "12345",
        },
    )
    assert ok.status_code == 200 and ok.text == "12345"
    bad = soc.c.get(
        soc.url(),
        params={"hub.mode": "subscribe", "hub.verify_token": "errado", "hub.challenge": "1"},
    )
    assert bad.status_code == 403


def test_signature_is_mandatory(soc: Social) -> None:
    raw = payload(soc.provider, [incoming(soc.provider, "m.1", "oi")])
    assert soc.post(raw, {"Content-Type": "application/json"}).status_code == 401
    assert soc.post(raw, sign(raw, "outro-segredo")).status_code == 401
    assert soc.env.sql("SELECT count(*) FROM messages WHERE tenant_id = %s", (soc.tid,)) == [(0,)]


def test_missing_app_secret_rejects_everything(client: TestClient, env: Env) -> None:
    for provider in BOTH:
        email = unique_email("nosec")
        env.tenant("Loja sem segredo", email)
        assert login(client, email).status_code == 200
        values = {k: v for k, v in CFG[provider].items() if k != "app_secret"}
        res = client.put(f"/v1/connections/{provider}", json={"values": values})
        assert res.status_code == 200, res.text
        pid = res.json()["webhook_url"].rsplit("/", 1)[1]
        raw = payload(provider, [incoming(provider, "m.x", "oi")])
        out = client.post(f"/v1/webhooks/{provider}/{pid}", content=raw, headers=sign(raw))
        assert out.status_code == 401 and out.json()["error"]["code"] == "app_secret_missing"


def test_inbound_creates_channel_only_contact_and_conversation_and_dedupes(soc: Social) -> None:
    assert soc.send_in("m.100", "Oi, quero saber do curso").json() == {
        "messages": 1,
        "statuses": 0,
        "opt_outs": 0,
        "duplicates": 0,
    }
    again = soc.send_in("m.100", "Oi, quero saber do curso")
    assert again.json()["duplicates"] == 1 and again.json()["messages"] == 0
    channel = CHANNEL[soc.provider]
    conv = soc.conv()
    assert conv[1] == channel
    rows = soc.env.sql(
        "SELECT ct.phone, ct.email, ct.channel_only, cc.channel, cc.external_id "
        "FROM contacts ct JOIN contact_channels cc ON cc.contact_id = ct.id "
        "WHERE ct.tenant_id = %s",
        (soc.tid,),
    )
    assert rows == [(None, None, True, channel, SENDER[soc.provider])]
    assert soc.env.sql(
        "SELECT body, status FROM messages WHERE tenant_id = %s AND direction = 'in'", (soc.tid,)
    ) == [("Oi, quero saber do curso", "received")]
    status = soc.env.sql("SELECT status FROM connections WHERE tenant_id = %s", (soc.tid,))
    assert ("connected",) in status
    soc.send_in("m.101", "mais uma")
    assert soc.env.sql("SELECT count(*) FROM conversations WHERE tenant_id = %s", (soc.tid,)) == [
        (1,)
    ]


def test_events_of_other_page_echo_and_attachments(soc: Social) -> None:
    other = payload(soc.provider, [incoming(soc.provider, "m.200", "oi")], entry_id="999999999999")
    assert soc.post(other).json()["messages"] == 0
    echo = incoming(soc.provider, "m.201", "eu mesmo enviei")
    echo["message"]["is_echo"] = True
    echo["sender"] = {"id": OWN[soc.provider]}
    assert soc.post(payload(soc.provider, [echo])).json()["messages"] == 0
    photo = incoming(soc.provider, "m.202", "")
    photo["message"] = {"mid": "m.202", "attachments": [{"type": "image"}]}
    assert soc.post(payload(soc.provider, [photo])).json()["messages"] == 1
    assert soc.env.sql(
        "SELECT body FROM messages WHERE tenant_id = %s AND direction = 'in'", (soc.tid,)
    ) == [("[mensagem do tipo image]",)]


def test_opt_out_blocks_the_person_and_only_the_confirmation_goes_out(
    soc: Social, db: Database
) -> None:
    out = soc.send_in("m.300", "SAIR").json()
    assert out["opt_outs"] == 1
    identity = f"{CHANNEL[soc.provider]}:{SENDER[soc.provider]}"
    assert soc.env.sql(
        "SELECT identity, reason FROM suppressions WHERE tenant_id = %s", (soc.tid,)
    ) == [(identity, "opt_out")]
    assert soc.conv()[2] == "closed"
    sim = SimulatedSender()
    stats = flush_outbox(db, soc.env.box, SimulatedSender(), social=both_senders(sim))
    assert stats.sent == 1 and len(sim.texts) == 1
    assert sim.texts[0][0] == SENDER[soc.provider] and "não vai mais receber" in sim.texts[0][1]
    # depois do pedido, nem a pessoa nem o vendedor IA conseguem mandar mais nada
    soc.queue("[social] oferta depois do pedido", author="human")
    blocked = flush_outbox(db, soc.env.box, SimulatedSender(), social=both_senders(sim))
    assert blocked.failed == 1 and len(sim.texts) == 1
    assert soc.cols("error", "[social] oferta depois do pedido") == [("nao_contatar",)]


def test_delivery_and_read_statuses_come_from_the_provider(soc: Social, db: Database) -> None:
    soc.send_in("m.400", "oi")
    soc.queue("[social] resposta")
    sim = SimulatedSender()
    flush_outbox(db, soc.env.box, SimulatedSender(), social=both_senders(sim))
    mid = soc.cols("provider_message_id", "[social] resposta")[0][0]

    def current() -> str:
        return str(soc.cols("status", "[social] resposta")[0][0])

    assert current() == "sent"
    delivery = {"sender": {"id": SENDER[soc.provider]}, "delivery": {"mids": [mid]}}
    assert soc.post(payload(soc.provider, [delivery])).json()["statuses"] == 1
    assert current() == "delivered"
    watermark = int((time.time() + 60) * 1000)
    read = {"sender": {"id": SENDER[soc.provider]}, "read": {"watermark": watermark}}
    soc.post(payload(soc.provider, [read]))
    assert current() == "read"
    soc.post(payload(soc.provider, [delivery]))  # evento atrasado nunca volta o estado
    assert current() == "read"


# -------------------------------------------------------------- fila de saída


def test_outbox_sends_to_the_person_id_inside_the_window_only(soc: Social, db: Database) -> None:
    soc.send_in("m.500", "oi")
    soc.queue("[social] resposta dentro da janela")
    wa_sender = SimulatedSender()
    sim = SimulatedSender()
    now = datetime.now(UTC) + timedelta(hours=1)
    stats = flush_outbox(db, soc.env.box, wa_sender, social=both_senders(sim), now=now)
    assert stats.sent == 1
    assert sim.texts == [(SENDER[soc.provider], "[social] resposta dentro da janela")]
    assert wa_sender.texts == []  # nunca pelo remetente do WhatsApp
    soc.queue("[social] tarde demais")
    late = flush_outbox(
        db,
        soc.env.box,
        wa_sender,
        social=both_senders(sim),
        now=datetime.now(UTC) + timedelta(hours=30),
    )
    assert late.failed == 1 and len(sim.texts) == 1
    assert soc.cols("error", "[social] tarde demais") == [("janela_24h_fechada",)]


def test_outbox_without_a_social_sender_never_uses_the_whatsapp_sender(
    soc: Social, db: Database
) -> None:
    soc.send_in("m.600", "oi")
    soc.queue("[social] não pode sair pelo WhatsApp")
    wa_sender = SimulatedSender()
    stats = flush_outbox(db, soc.env.box, wa_sender)  # sem remetente social
    assert stats.claimed == 0 and wa_sender.texts == []
    assert soc.cols("status", "[social] não pode sair pelo WhatsApp") == [("queued",)]


def test_outbox_unavailable_social_sender_keeps_messages_queued(soc: Social, db: Database) -> None:
    soc.send_in("m.610", "oi")
    soc.queue("[social] fica na fila")
    unavailable = {"messenger": UnavailableSender(), "instagram": UnavailableSender()}
    stats = flush_outbox(db, soc.env.box, UnavailableSender(), social=unavailable)
    assert stats.claimed == 0
    assert soc.cols("status", "[social] fica na fila") == [("queued",)]


def test_outbox_sends_only_once_and_marks_refusal_as_failed(soc: Social, db: Database) -> None:
    soc.send_in("m.620", "oi")
    soc.queue("[social] uma vez só")
    sim = SimulatedSender()
    flush_outbox(db, soc.env.box, SimulatedSender(), social=both_senders(sim))
    flush_outbox(db, soc.env.box, SimulatedSender(), social=both_senders(sim))
    assert [t for _, t in sim.texts].count("[social] uma vez só") == 1
    soc.queue("[social] vai ser recusada")
    refusing = SimulatedSender(fail_with="meta 10: sem permissão")
    flush_outbox(db, soc.env.box, SimulatedSender(), social=both_senders(refusing))
    assert soc.cols("status, error", "[social] vai ser recusada") == [
        ("failed", "meta 10: sem permissão")
    ]


def test_outbox_fails_when_channel_is_not_connected(
    client: TestClient, env: Env, db: Database
) -> None:
    soc = Social(client, env, "messenger")
    soc.send_in("m.630", "oi")
    soc.queue("[social] sem conexão")
    env.sql("UPDATE connections SET status = 'needs_attention' WHERE tenant_id = %s", (soc.tid,))
    sim = SimulatedSender()
    flush_outbox(db, env.box, SimulatedSender(), social=both_senders(sim))
    assert soc.cols("error", "[social] sem conexão") == [("canal_nao_conectado",)]
    assert sim.texts == []


# ------------------------------------------------------------- vendedor IA


def test_seller_ai_answers_a_social_conversation_and_respects_opt_out(
    soc: Social, db: Database
) -> None:
    env = soc.env
    env.sql("UPDATE messages SET handled = true WHERE direction = 'in'")
    env.sql(
        "INSERT INTO tenant_settings (tenant_id, ai_enabled, ai_persona) "
        "VALUES (%s, true, 'Simpático') ON CONFLICT (tenant_id) DO UPDATE SET ai_enabled = true",
        (soc.tid,),
    )
    env.sql(
        "INSERT INTO offers (tenant_id, name, description, price_cents, payment_url) "
        "VALUES (%s, 'Curso X', 'Aprenda X', 19700, 'https://pay.example.test/x')",
        (soc.tid,),
    )
    soc.send_in("m.700", "quanto custa?")
    stats = run_ai_replies(db, SimulatedAiModel())
    assert stats.replied == 1
    out = env.sql(
        "SELECT author, status FROM messages WHERE tenant_id = %s AND direction = 'out'",
        (soc.tid,),
    )
    assert out == [("bot", "queued")]
    # bloqueio por identidade do canal: depois do SAIR o vendedor não responde
    soc.send_in("m.701", "SAIR")
    soc.send_in("m.702", "quanto custa mesmo?")
    again = run_ai_replies(db, SimulatedAiModel())
    assert again.replied == 0


def test_social_conversations_never_become_cold_opportunities(soc: Social, db: Database) -> None:
    env = soc.env
    env.sql(
        "INSERT INTO tenant_settings (tenant_id, recovery_enabled, cold_enabled, "
        "consent_declared_at) VALUES (%s, true, true, now()) "
        "ON CONFLICT (tenant_id) DO UPDATE SET recovery_enabled = true, cold_enabled = true, "
        "consent_declared_at = now()",
        (soc.tid,),
    )
    soc.send_in("m.800", "oi")
    soc.queue("[social] resposta que esfriou", author="human")
    flush_outbox(db, env.box, SimulatedSender(), social=both_senders(SimulatedSender()))
    env.sql(
        "UPDATE messages SET created_at = now() - interval '30 hours' WHERE tenant_id = %s",
        (soc.tid,),
    )
    assert detect_cold_conversations(db) == 0
    assert env.sql("SELECT count(*) FROM recovery_cases WHERE tenant_id = %s", (soc.tid,)) == [(0,)]


# --------------------------------------------------------- painel / API


def test_inbox_shows_the_channel_and_masks_the_person_id(soc: Social) -> None:
    soc.send_in("m.900", "oi, tudo bem?")
    inbox = soc.c.get("/v1/inbox").json()
    item = next(i for i in inbox if i["id"] == str(soc.conv()[0]))
    assert item["channel"] == CHANNEL[soc.provider]
    assert item["phone"].endswith(SENDER[soc.provider][-4:])
    assert SENDER[soc.provider] not in json.dumps(item)
    detail = soc.c.get(f"/v1/inbox/{soc.conv()[0]}").json()
    assert detail["channel"] == CHANNEL[soc.provider]
    assert SENDER[soc.provider] not in json.dumps(detail)


def test_human_reply_outside_window_explains_it_is_not_a_template_case(
    soc: Social, db: Database
) -> None:
    soc.send_in("m.910", "oi")
    soc.env.sql(
        "UPDATE conversations SET last_inbound_at = now() - interval '30 hours' "
        "WHERE tenant_id = %s",
        (soc.tid,),
    )
    res = soc.c.post(f"/v1/inbox/{soc.conv()[0]}/reply", json={"body": "oi"})
    assert res.status_code == 400 and res.json()["error"]["code"] == "window_closed"
    assert "template" not in res.json()["error"]["message"]


def test_catalog_asks_for_the_channel_fields_and_listing_hides_secrets(
    client: TestClient, env: Env
) -> None:
    for key, needed in (
        ("messenger", "page_access_token"),
        ("instagram_dm", "instagram_account_id"),
    ):
        provider = get_provider(key)
        assert provider is not None
        keys = [f.key for f in provider.fields]
        assert "app_secret" in keys and needed in keys
        secrets = {f.key for f in provider.fields if f.secret}
        assert {"app_secret"} <= secrets
    soc = Social(client, env, "messenger")
    listing = soc.c.get("/v1/connections").text
    assert TOKEN not in listing and APP_SECRET not in listing and soc.verify_token not in listing
    assert "messenger" in listing
    blob = bytes(
        env.sql("SELECT config_encrypted FROM connections WHERE tenant_id = %s", (soc.tid,))[0][0]
    )
    assert TOKEN.encode() not in blob and APP_SECRET.encode() not in blob


# --------------------------------------------------------- isolamento por cliente


def test_contact_channels_are_isolated_by_tenant(
    client: TestClient, env: Env, db: Database
) -> None:
    a = Social(client, env, "messenger")
    a.send_in("m.a1", "oi")
    b = Social(client, env, "messenger")
    b.send_in("m.b1", "oi")
    assert a.tid != b.tid
    with db.tx(tenant_id=uuid.UUID(a.tid)) as conn:
        rows = conn.execute("SELECT tenant_id FROM contact_channels").fetchall()
    assert rows and {str(r["tenant_id"]) for r in rows} == {a.tid}
    with db.tx(tenant_id=uuid.UUID(b.tid)) as conn:
        denied = conn.execute(
            "SELECT count(*) AS n FROM contact_channels WHERE tenant_id = %s", (a.tid,)
        ).fetchone()
    assert denied is not None and denied["n"] == 0
    # o mesmo ID de pessoa em dois clientes gera dois contatos, um em cada cliente
    assert env.sql("SELECT count(DISTINCT tenant_id) FROM contact_channels")[0][0] >= 2


# --------------------------------------------------- remetente real (servidor falso)


def _sender(fake: Fake, provider: str) -> SocialSender:
    return SocialSender(fake.client(), SOCIAL[provider])


def test_messenger_send_payload_and_token_only_in_header() -> None:
    fake = Fake(body={"recipient_id": PSID, "message_id": "m_AbC"})
    mid = _sender(fake, "messenger").send_text(CFG["messenger"], PSID, "olá")
    assert mid == "m_AbC"
    req = fake.requests[0]
    assert req.method == "POST" and req.url.path.endswith(f"/{PAGE}/messages")
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(req.url) and TOKEN not in req.content.decode()
    assert fake.json() == {
        "recipient": {"id": PSID},
        "messaging_type": "RESPONSE",
        "message": {"text": "olá"},
    }


def test_instagram_send_uses_the_account_id() -> None:
    fake = Fake(body={"recipient_id": IGSID, "message_id": "aWdfZAG1"})
    mid = _sender(fake, "instagram_dm").send_text(CFG["instagram_dm"], IGSID, "oi")
    assert mid == "aWdfZAG1" and fake.requests[0].url.path.endswith(f"/{IG}/messages")


def test_send_refused_by_meta_is_a_clean_send_error_without_the_token() -> None:
    body = {"error": {"message": "(#551) This person isn't available right now", "code": 551}}
    fake = Fake(status=400, body=body)
    with pytest.raises(SendError) as err:
        _sender(fake, "messenger").send_text(CFG["messenger"], PSID, "oi")
    assert "551" in str(err.value) and TOKEN not in str(err.value)


def test_uncertain_send_is_not_a_send_error_and_is_never_retried() -> None:
    for fake in (Fake(status=503, body={}), Fake(raises=True), Fake(body={"recipient_id": "1"})):
        with pytest.raises(Exception) as err:
            _sender(fake, "messenger").send_text(CFG["messenger"], PSID, "oi")
        assert not isinstance(err.value, SendError)
        assert len(fake.requests) == 1


def test_send_without_account_or_token_is_refused_before_any_request() -> None:
    fake = Fake(body={})
    with pytest.raises(SendError):
        _sender(fake, "messenger").send_text({"page_id": PAGE}, PSID, "oi")
    assert fake.requests == []


def test_social_channels_have_no_templates() -> None:
    from fm_seller.recovery.senders import OutboundMessage

    msg = OutboundMessage(uuid.uuid4(), "1", "k", "corpo")
    with pytest.raises(SendError):
        _sender(Fake(body={}), "messenger").send({}, msg)


def _settings(env: str, live: bool) -> Settings:
    return Settings(
        env=cast(Any, env),
        whatsapp_live=live,
        secrets_keys="x",
        google_client_id="id.apps.googleusercontent.com",
        cookie_secure=True,
        web_origin="https://app.example.test",
        public_base_url="https://api.example.test",
        database_url="postgresql://fm_app:senha@db.example.test:5432/fm",
        database_admin_url="postgresql://fm_owner:senha@db.example.test:5432/fm",
    )


def test_social_sender_selection_follows_the_environment() -> None:
    assert all(
        isinstance(s, SimulatedSender)
        for s in build_social_senders(_settings("test", True)).values()
    )
    assert all(
        isinstance(s, SimulatedSender)
        for s in build_social_senders(_settings("dev", False)).values()
    )
    staging = build_social_senders(_settings("staging", False))
    assert set(staging) == {"messenger", "instagram"}
    assert all(isinstance(s, UnavailableSender) for s in staging.values())
    live = build_social_senders(_settings("staging", True))
    assert all(isinstance(s, SocialSender) for s in live.values())
    assert set(BY_CHANNEL) == {"messenger", "instagram"}


# ------------------------------------------------------------ teste de conexão


def test_connection_tester_reads_the_page_and_the_account() -> None:
    page = get_provider("messenger")
    assert page is not None
    fake = Fake(body={"name": "Loja do Fábio", "id": PAGE})
    res = MetaConnectionTester(fake.client()).test(page, CFG["messenger"])
    assert res.ok and "Loja do Fábio" in res.message
    assert fake.requests[0].method == "GET" and fake.requests[0].url.path.endswith(f"/{PAGE}")
    assert TOKEN not in str(fake.requests[0].url)
    ig = get_provider("instagram_dm")
    assert ig is not None
    fake2 = Fake(body={"username": "lojadofabio", "id": IG})
    res2 = MetaConnectionTester(fake2.client()).test(ig, CFG["instagram_dm"])
    assert res2.ok and "lojadofabio" in res2.message


def test_connection_tester_failure_is_honest_and_hides_the_token() -> None:
    page = get_provider("messenger")
    assert page is not None
    bad = Fake(status=400, body={"error": {"message": "Invalid OAuth access token.", "code": 190}})
    res = MetaConnectionTester(bad.client()).test(page, CFG["messenger"])
    assert not res.ok and "190" in res.message and TOKEN not in res.message
    down = MetaConnectionTester(Fake(raises=True).client()).test(page, CFG["messenger"])
    assert not down.ok
    missing = MetaConnectionTester(Fake(body={}).client()).test(page, {"page_id": PAGE})
    assert not missing.ok and "Faltam" in missing.message


def test_meta_client_type_is_reused() -> None:
    assert isinstance(Fake(body={}).client(), MetaClient)
    assert isinstance(httpx.MockTransport(Fake()), httpx.MockTransport)
