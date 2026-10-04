"""WhatsApp real (Graph API) e templates da Meta, sempre contra servidor falso (MockTransport).

Nada aqui prova a integração real: o formato vem da documentação da Meta e só uma conta real
confirma. Ver docs/PENDENCIAS_EXTERNAS.md.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from fm_seller.channels.meta_api import MetaClient, MetaRejected, MetaUncertain
from fm_seller.channels.templates_gateway import (
    GatewayRejected,
    GatewayUncertain,
    MetaTemplateGateway,
    RemoteTemplate,
    SimulatedTemplateGateway,
    UnavailableTemplateGateway,
    build_template_gateway,
)
from fm_seller.channels.whatsapp_send import WhatsAppCloudSender
from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.providers.catalog import PROVIDERS
from fm_seller.providers.testers import (
    MetaConnectionTester,
    SimulatedTester,
    UnavailableTester,
    build_tester,
)
from fm_seller.recovery.senders import (
    OutboundMessage,
    SendError,
    SimulatedSender,
    UnavailableSender,
    build_sender,
)
from fm_seller.recovery.template_sync import sync_all
from fm_seller.recovery.templates import meta_name_for, sanitize_param, to_meta
from tests.conftest import Env, login, unique_email
from tests.test_recovery_engine import NOW, Ctx, ev
from tests.test_recovery_engine import ctx as ctx

TOKEN = "EAAGsegredo-super-secreto-9876"
CFG = {"phone_number_id": "1055550001", "waba_id": "2077770002", "access_token": TOKEN}
TID = uuid.uuid4()


class Fake:
    """Servidor falso da Graph API: guarda as requisições e responde o que o teste mandar."""

    def __init__(self, status: int = 200, body: Any = None, raises: bool = False) -> None:
        self.status, self.body, self.raises = status, body, raises
        self.requests: list[httpx.Request] = []
        self.queue: list[tuple[int, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raises:
            raise httpx.ConnectError("sem rede")
        status, body = self.queue.pop(0) if self.queue else (self.status, self.body)
        return httpx.Response(status, json=body)

    def client(self) -> MetaClient:
        return MetaClient(transport=httpx.MockTransport(self))

    def json(self, i: int = 0) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.requests[i].content))


OK_SEND = {"messaging_product": "whatsapp", "messages": [{"id": "wamid.ABC"}]}


def _msg(**kw: Any) -> OutboundMessage:
    base: dict[str, Any] = {
        "tenant_id": TID,
        "to_phone": "5511999998888",
        "template_key": "pix_1",
        "body": "texto",
        "meta_name": "pix_1_v1",
        "language": "pt_BR",
        "params": ("Ana", "Curso", "R$ 97,00", "https://pay.example.test/c"),
    }
    return OutboundMessage(**{**base, **kw})


# ------------------------------------------------------------ conversão de texto


def test_to_meta_numbers_variables_in_order_of_appearance() -> None:
    text, names = to_meta("Oi {nome}, seu {produto} ({valor}) {link} {nome}")
    assert text == "Oi {{1}}, seu {{2}} ({{3}}) {{4}} {{5}}"
    assert names == ["nome", "produto", "valor", "link", "nome"]
    assert to_meta("sem variável") == ("sem variável", [])


def test_sanitize_param_and_version_name() -> None:
    assert sanitize_param("a\n b\t\tc   d ") == "a b c d"
    assert len(sanitize_param("x" * 2000)) == 1024
    assert meta_name_for("pix_1", 3) == "pix_1_v3"


# ------------------------------------------------------------------ envio real


def test_send_template_request_shape_and_token_only_in_header() -> None:
    fake = Fake(200, OK_SEND)
    wid = WhatsAppCloudSender(fake.client()).send(CFG, _msg())
    assert wid == "wamid.ABC"
    req = fake.requests[0]
    assert req.method == "POST" and req.url.path == "/v26.0/1055550001/messages"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(req.url) and TOKEN.encode() not in req.content
    body = fake.json()
    assert body["messaging_product"] == "whatsapp" and body["type"] == "template"
    assert body["to"] == "5511999998888"
    assert body["template"]["name"] == "pix_1_v1"
    assert body["template"]["language"] == {"code": "pt_BR"}
    params = body["template"]["components"][0]
    assert params["type"] == "body"
    assert [p["text"] for p in params["parameters"]][:2] == ["Ana", "Curso"]


def test_send_template_without_variables_has_no_components() -> None:
    fake = Fake(200, OK_SEND)
    WhatsAppCloudSender(fake.client()).send(CFG, _msg(params=()))
    assert "components" not in fake.json()["template"]


def test_send_text_request_shape() -> None:
    fake = Fake(200, OK_SEND)
    assert WhatsAppCloudSender(fake.client()).send_text(CFG, "5511999998888", "Oi!") == "wamid.ABC"
    body = fake.json()
    assert body["type"] == "text" and body["text"] == {"body": "Oi!", "preview_url": False}


def test_send_refuses_before_any_request() -> None:
    fake = Fake(200, OK_SEND)
    sender = WhatsAppCloudSender(fake.client())
    with pytest.raises(SendError):
        sender.send(CFG, _msg(meta_name=None))
    with pytest.raises(SendError):
        sender.send(CFG, _msg(params=("Ana", "", "x", "y")))  # parâmetro vazio
    with pytest.raises(SendError):
        sender.send({"phone_number_id": "1"}, _msg())  # sem token
    assert fake.requests == []


def test_send_clear_rejection_is_send_error_with_portuguese_label_and_no_token() -> None:
    err = {"error": {"message": "Re-engagement message", "code": 131047}}
    fake = Fake(400, err)
    with pytest.raises(SendError) as exc:
        WhatsAppCloudSender(fake.client()).send(CFG, _msg())
    assert "janela de 24 h" in str(exc.value) and TOKEN not in str(exc.value)
    assert len(fake.requests) == 1  # sem nova tentativa


@pytest.mark.parametrize(
    "fake",
    [
        Fake(500, {"error": {"message": "boom"}}),
        Fake(raises=True),
        Fake(200, {"messaging_product": "whatsapp"}),  # 2xx sem id
        Fake(200, {"messages": []}),
    ],
)
def test_uncertain_outcomes_are_not_send_error_and_never_retried(fake: Fake) -> None:
    with pytest.raises(Exception) as exc:
        WhatsAppCloudSender(fake.client()).send(CFG, _msg())
    assert not isinstance(exc.value, SendError)  # o motor marca "incerto" e não reenvia
    assert TOKEN not in str(exc.value)
    assert len(fake.requests) == 1


def test_meta_client_error_classes_do_not_leak_token() -> None:
    fake = Fake(401, {"error": {"message": f"token {TOKEN} inválido", "code": 190}})
    with pytest.raises(MetaRejected) as exc:
        fake.client().request("GET", "x", TOKEN)
    assert exc.value.status == 401 and exc.value.code == 190
    with pytest.raises(MetaUncertain):
        Fake(raises=True).client().request("GET", "x", TOKEN)
    with pytest.raises(MetaUncertain):
        Fake(200, ["não é objeto"]).client().request("GET", "x", TOKEN)


# ------------------------------------------------------- motor passa os parâmetros


def test_engine_passes_meta_name_language_and_ordered_params(ctx: Ctx) -> None:
    ctx.env.sql(
        "UPDATE message_templates SET body = 'Oi {nome}! {produto} por {valor}: {link} ok', "
        "meta_name = 'pix_1_v2', meta_language = 'pt_BR' WHERE tenant_id = %s AND key = 'pix_1'",
        (str(ctx.tid),),
    )
    ctx.handle(ev(kind="pix_pending"))
    ctx.run(NOW + timedelta(minutes=16))
    sent = ctx.sender.sent
    assert len(sent) == 1
    assert sent[0].meta_name == "pix_1_v2" and sent[0].language == "pt_BR"
    assert sent[0].params == ("Ana", "Curso", "R$ 97,00", "https://pay.example.test/c")


# ---------------------------------------------------------------- gateway Meta


def _gw(fake: Fake) -> MetaTemplateGateway:
    return MetaTemplateGateway(fake.client())


def test_gateway_create_request_shape() -> None:
    fake = Fake(200, {"id": "999", "status": "PENDING", "category": "MARKETING"})
    out = _gw(fake).create(
        CFG,
        name="pix_1_v1",
        language="pt_BR",
        category="MARKETING",
        text="Oi {{1}}, veja {{2}}",
        examples=["Maria", "Curso"],
    )
    assert (out.id, out.status, out.name) == ("999", "PENDING", "pix_1_v1")
    req = fake.requests[0]
    assert req.method == "POST" and req.url.path == "/v26.0/2077770002/message_templates"
    assert req.headers["authorization"] == f"Bearer {TOKEN}" and TOKEN.encode() not in req.content
    body = fake.json()
    assert body["name"] == "pix_1_v1" and body["language"] == "pt_BR"
    assert body["category"] == "MARKETING" and body["allow_category_change"] is True
    assert body["components"] == [
        {
            "type": "BODY",
            "text": "Oi {{1}}, veja {{2}}",
            "example": {"body_text": [["Maria", "Curso"]]},
        }
    ]


def test_gateway_create_without_variables_has_no_example() -> None:
    fake = Fake(200, {"id": "1", "status": "PENDING"})
    _gw(fake).create(CFG, name="a_v1", language="pt_BR", category="UTILITY", text="Oi", examples=[])
    assert "example" not in fake.json()["components"][0]


def test_gateway_create_error_mapping() -> None:
    rej = Fake(400, {"error": {"message": "Invalid parameter", "code": 100}})
    with pytest.raises(GatewayRejected):
        _gw(rej).create(CFG, name="a", language="pt_BR", category="UTILITY", text="x", examples=[])
    for unc in (Fake(503, {}), Fake(raises=True), Fake(200, {"id": "1"})):
        with pytest.raises(GatewayUncertain):
            _gw(unc).create(
                CFG, name="a", language="pt_BR", category="UTILITY", text="x", examples=[]
            )
    with pytest.raises(GatewayRejected):  # sem WABA/token: nem chama
        _gw(rej).create(
            {"access_token": TOKEN},
            name="a",
            language="pt_BR",
            category="UTILITY",
            text="x",
            examples=[],
        )


def test_gateway_list_follows_pages_and_ignores_none_reason() -> None:
    fake = Fake()
    fake.queue = [
        (
            200,
            {
                "data": [
                    {"id": "1", "name": "a_v1", "language": "pt_BR", "status": "approved"},
                    {"junk": True},
                ],
                "paging": {"cursors": {"after": "CUR1"}, "next": "https://x"},
            },
        ),
        (
            200,
            {
                "data": [
                    {
                        "id": "2",
                        "name": "b_v1",
                        "language": "pt_BR",
                        "status": "REJECTED",
                        "rejected_reason": "INVALID_FORMAT",
                    },
                    {
                        "id": "3",
                        "name": "c_v1",
                        "language": "pt_BR",
                        "status": "APPROVED",
                        "rejected_reason": "NONE",
                    },
                ],
                "paging": {"cursors": {"after": "CUR2"}},
            },
        ),
    ]
    items = _gw(fake).list(CFG)
    assert [(t.name, t.status, t.reason) for t in items] == [
        ("a_v1", "APPROVED", None),
        ("b_v1", "REJECTED", "INVALID_FORMAT"),
        ("c_v1", "APPROVED", None),
    ]
    assert len(fake.requests) == 2
    assert fake.requests[1].url.params["after"] == "CUR1"
    assert fake.requests[0].url.params["fields"].startswith("name,status")


# --------------------------------------------------------- teste de conexão real


def test_meta_connection_tester_ok_and_failures() -> None:
    wa = next(p for p in PROVIDERS if p.key == "whatsapp_cloud")
    fake = Fake()
    fake.queue = [
        (200, {"display_phone_number": "+55 11 99999-8888", "verified_name": "Loja X"}),
        (200, {"name": "Conta"}),
    ]
    res = MetaConnectionTester(fake.client()).test(wa, CFG)
    assert res.ok and "+55 11 99999-8888" in res.message and "Loja X" in res.message
    assert all(r.method == "GET" for r in fake.requests)  # só leitura
    assert fake.requests[0].url.path.endswith("/1055550001")
    assert fake.requests[1].url.path.endswith("/2077770002")

    bad = MetaConnectionTester(
        Fake(401, {"error": {"message": "Invalid OAuth", "code": 190}}).client()
    )
    out = bad.test(wa, CFG)
    assert not out.ok and "190" in out.message and TOKEN not in out.message
    down = MetaConnectionTester(Fake(raises=True).client()).test(wa, CFG)
    assert not down.ok and TOKEN not in down.message
    assert not MetaConnectionTester(Fake().client()).test(wa, {"access_token": TOKEN}).ok


def test_meta_connection_tester_falls_back_for_other_providers() -> None:
    # Messenger e Instagram têm teste real próprio (tests/test_social_channels.py).
    other = next(p for p in PROVIDERS if p.key == "cakto")
    fake = Fake()
    res = MetaConnectionTester(fake.client()).test(other, {})
    assert not res.ok and "não foi confirmada" in res.message and fake.requests == []


# ------------------------------------------------- seleção por configuração


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


def test_real_adapters_are_never_selected_in_test_and_only_when_live() -> None:
    assert isinstance(build_sender(_settings("test", True)), SimulatedSender)
    assert isinstance(build_sender(_settings("dev", False)), SimulatedSender)
    assert isinstance(build_sender(_settings("staging", False)), UnavailableSender)
    assert isinstance(build_sender(_settings("staging", True)), WhatsAppCloudSender)
    assert isinstance(build_sender(_settings("prod", True)), WhatsAppCloudSender)

    assert isinstance(build_template_gateway(_settings("test", True)), SimulatedTemplateGateway)
    assert isinstance(
        build_template_gateway(_settings("staging", False)), UnavailableTemplateGateway
    )
    assert isinstance(build_template_gateway(_settings("prod", True)), MetaTemplateGateway)
    dev = build_template_gateway(_settings("dev", False))
    assert isinstance(dev, SimulatedTemplateGateway) and dev.auto_approve

    assert isinstance(build_tester(_settings("test", True)), SimulatedTester)
    assert isinstance(build_tester(_settings("staging", False)), UnavailableTester)
    assert isinstance(build_tester(_settings("prod", True)), MetaConnectionTester)


# -------------------------------------------------------- envio e sincronização


@pytest.fixture
def gw(client: TestClient) -> SimulatedTemplateGateway:
    gateway = SimulatedTemplateGateway()
    client.app.state.templates = gateway  # type: ignore[attr-defined]
    return gateway


def _owner(client: TestClient, env: Env, connected: bool = True) -> str:
    email = unique_email("tpl")
    tid = env.tenant("Loja Templates", email)
    assert login(client, email).status_code == 200
    if connected:
        res = client.put("/v1/connections/whatsapp_cloud", json={"values": CFG})
        assert res.status_code == 200, res.text
        env.sql("UPDATE connections SET status = 'connected' WHERE tenant_id = %s", (tid,))
    return tid


def _save(client: TestClient, key: str, body: str) -> None:
    assert client.put(f"/v1/recovery/templates/{key}", json={"body": body}).status_code == 200


def _tpl(client: TestClient, key: str) -> dict[str, Any]:
    return next(t for t in client.get("/v1/recovery/templates").json() if t["key"] == key)


def test_submit_creates_versioned_name_with_positional_text_and_samples(
    client: TestClient, env: Env, gw: SimulatedTemplateGateway
) -> None:
    _owner(client, env)
    _save(client, "pix_1", "Oi {nome}! Seu PIX de {produto} está aberto, link: {link} obrigado")
    res = client.post("/v1/recovery/templates/pix_1/submit", json={"category": "UTILITY"})
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["meta_name"] == "pix_1_v1" and out["meta_status"] == "submitted"
    assert out["meta_category"] == "UTILITY" and out["note"] is None
    sent = gw.created[0]
    assert sent["text"] == "Oi {{1}}! Seu PIX de {{2}} está aberto, link: {{3}} obrigado"
    assert sent["examples"] == ["Maria", "Curso de exemplo", "https://exemplo.com.br/pagar"]
    # já enviado: não reenvia
    again = client.post("/v1/recovery/templates/pix_1/submit", json={"category": "UTILITY"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_submitted"
    assert len(gw.created) == 1


def test_sync_maps_statuses_and_reason(
    client: TestClient, env: Env, gw: SimulatedTemplateGateway
) -> None:
    _owner(client, env)
    for key in ("a_1", "b_1", "c_1", "d_1"):
        _save(client, key, f"Olá {{nome}}, texto {key} fim")
        assert client.post(f"/v1/recovery/templates/{key}/submit", json={}).status_code == 200
    waba = CFG["waba_id"]
    gw.set_status(waba, "a_1_v1", "APPROVED")
    gw.set_status(waba, "b_1_v1", "REJECTED", "INVALID_FORMAT")
    gw.set_status(waba, "c_1_v1", "PAUSED", "LOW_QUALITY")
    gw.set_status(waba, "d_1_v1", "ALGO_NOVO")  # desconhecido: não altera
    res = client.post("/v1/recovery/templates/sync")
    assert res.status_code == 200, res.text
    assert res.json()["checked"] == 4
    assert _tpl(client, "a_1")["meta_status"] == "approved"
    b = _tpl(client, "b_1")
    assert b["meta_status"] == "rejected" and b["meta_reason"] == "INVALID_FORMAT"
    assert _tpl(client, "c_1")["meta_status"] == "paused"
    assert _tpl(client, "d_1")["meta_status"] == "submitted"
    assert _tpl(client, "a_1")["synced_at"] is not None
    gw.set_status(waba, "a_1_v1", "DISABLED")
    client.post("/v1/recovery/templates/sync")
    assert _tpl(client, "a_1")["meta_status"] == "disabled"


def test_rejected_template_can_be_edited_and_resubmitted_with_new_version(
    client: TestClient, env: Env, gw: SimulatedTemplateGateway
) -> None:
    _owner(client, env)
    _save(client, "r_1", "Texto {nome} com problema agora")
    client.post("/v1/recovery/templates/r_1/submit", json={})
    gw.set_status(CFG["waba_id"], "r_1_v1", "REJECTED", "INVALID_FORMAT")
    client.post("/v1/recovery/templates/sync")
    # sem editar, pode reenviar (rejeitado): ganha nome novo
    again = client.post("/v1/recovery/templates/r_1/submit", json={}).json()
    assert again["meta_name"] == "r_1_v2" and again["meta_reason"] is None


def test_editing_body_drops_meta_link_and_approval(
    client: TestClient, env: Env, gw: SimulatedTemplateGateway
) -> None:
    _owner(client, env)
    _save(client, "e_1", "Texto {nome} original completo")
    client.post("/v1/recovery/templates/e_1/submit", json={})
    gw.set_status(CFG["waba_id"], "e_1_v1", "APPROVED")
    client.post("/v1/recovery/templates/sync")
    assert _tpl(client, "e_1")["meta_status"] == "approved"
    _save(client, "e_1", "Texto {nome} original completo")  # igual: mantém
    assert _tpl(client, "e_1")["meta_status"] == "approved"
    _save(client, "e_1", "Texto {nome} alterado completo")
    t = _tpl(client, "e_1")
    assert t["meta_status"] == "draft" and t["meta_name"] is None and t["meta_reason"] is None
    v2 = client.post("/v1/recovery/templates/e_1/submit", json={}).json()
    assert v2["meta_name"] == "e_1_v2"  # a versão nunca volta atrás


def test_submit_rejected_by_meta_is_recorded_with_reason(
    client: TestClient, env: Env, gw: SimulatedTemplateGateway
) -> None:
    _owner(client, env)
    gw.fail_create = "Parâmetro inválido (código 100)"
    _save(client, "f_1", "Texto {nome} qualquer coisa")
    out = client.post("/v1/recovery/templates/f_1/submit", json={}).json()
    assert out["meta_status"] == "rejected" and "inválido" in out["meta_reason"]


def test_uncertain_submit_stays_submitted_and_sync_finds_it_by_name(
    client: TestClient, env: Env, gw: SimulatedTemplateGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    _owner(client, env)
    _save(client, "u_1", "Texto {nome} incerto aqui")

    def boom(*_a: Any, **_k: Any) -> Any:
        raise GatewayUncertain("rede")

    real_create = gw.create
    monkeypatch.setattr(gw, "create", boom)
    out = client.post("/v1/recovery/templates/u_1/submit", json={}).json()
    assert out["meta_status"] == "submitted" and out["note"] == "pending_confirmation"
    # a Meta chegou a criar: a sincronização acha pelo nome
    monkeypatch.setattr(gw, "create", real_create)
    real_create(CFG, name="u_1_v1", language="pt_BR", category="MARKETING", text="x", examples=[])
    gw.set_status(CFG["waba_id"], "u_1_v1", "APPROVED")
    client.post("/v1/recovery/templates/sync")
    assert _tpl(client, "u_1")["meta_status"] == "approved"


def test_submit_needs_connection_role_plan_and_enabled_gateway(
    client: TestClient, env: Env, gw: SimulatedTemplateGateway
) -> None:
    _owner(client, env, connected=False)
    _save(client, "g_1", "Texto {nome} sem conexão")
    res = client.post("/v1/recovery/templates/g_1/submit", json={})
    assert res.status_code == 400 and res.json()["error"]["code"] == "channel_not_connected"
    assert client.post("/v1/recovery/templates/nao_salvo/submit", json={}).status_code in (400, 404)
    assert (
        client.post("/v1/recovery/templates/g_1/submit", json={"category": "X"}).status_code == 400
    )

    client.app.state.templates = UnavailableTemplateGateway()  # type: ignore[attr-defined]
    off = client.post("/v1/recovery/templates/g_1/submit", json={})
    assert off.status_code == 400 and off.json()["error"]["code"] == "meta_disabled"
    assert client.post("/v1/recovery/templates/sync").json()["error"]["code"] == "meta_disabled"


def test_submit_and_sync_forbidden_for_agent_role(
    client: TestClient, env: Env, gw: SimulatedTemplateGateway
) -> None:
    tid = _owner(client, env)
    agent = unique_email("agente")
    env.invite(tid, agent, "agent")
    client.post("/v1/auth/logout")
    assert login(client, agent).status_code == 200
    assert client.post("/v1/recovery/templates/x_1/submit", json={}).status_code == 403
    assert client.post("/v1/recovery/templates/sync").status_code == 403


def test_templates_are_isolated_between_tenants_on_sync(
    client: TestClient, env: Env, gw: SimulatedTemplateGateway
) -> None:
    _owner(client, env)
    _save(client, "iso_1", "Texto {nome} do cliente um")
    client.post("/v1/recovery/templates/iso_1/submit", json={})
    client.post("/v1/auth/logout")
    _owner(client, env)  # outro cliente, mesma conta Meta de teste
    assert "iso_1" not in {t["key"] for t in client.get("/v1/recovery/templates").json()}
    assert client.post("/v1/recovery/templates/sync").json() == {"checked": 0, "updated": 0}


# ------------------------------------------------------------------ worker


def test_worker_sync_all_throttles_and_updates(
    client: TestClient,
    env: Env,
    db: Database,
    gw: SimulatedTemplateGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = _owner(client, env)
    _save(client, "w_1", "Texto {nome} do worker aqui")
    client.post("/v1/recovery/templates/w_1/submit", json={})
    gw.set_status(CFG["waba_id"], "w_1_v1", "APPROVED")
    env.sql("UPDATE message_templates SET meta_synced_at = NULL WHERE tenant_id = %s", (tid,))
    first = sync_all(db, env.box, gw)
    assert first.updated >= 1
    assert _tpl(client, "w_1")["meta_status"] == "approved"
    # recém sincronizado e já aprovado: não consulta de novo antes de 30 min
    gw.created.clear()
    calls: list[int] = []
    original = gw.list

    def counting(config: dict[str, str]) -> list[RemoteTemplate]:
        calls.append(1)
        return original(config)

    monkeypatch.setattr(gw, "list", counting)
    sync_all(db, env.box, gw)
    assert calls == []
    env.sql(
        "UPDATE message_templates SET meta_synced_at = now() - interval '40 minutes' "
        "WHERE tenant_id = %s",
        (tid,),
    )
    sync_all(db, env.box, gw)
    assert calls == [1]


def test_worker_sync_all_skips_when_gateway_unavailable(env: Env, db: Database) -> None:
    out = sync_all(db, env.box, UnavailableTemplateGateway())
    assert (out.checked, out.updated) == (0, 0)
