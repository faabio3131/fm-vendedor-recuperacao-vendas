"""Captura segura de eventos reais de checkout e conferência campo a campo.

Payloads sintéticos: o objetivo é provar que a captura não expõe ninguém e que a conferência diz
o que falta, não confirmar o formato real (isso só um evento real faz).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fm_seller import cli
from fm_seller.api.app import create_app
from fm_seller.config import Settings, get_settings
from fm_seller.db import Database
from fm_seller.events import capture, compare
from fm_seller.events.capture import CaptureConfig
from fm_seller.events.ingest import auth_method
from fm_seller.events.normalize import NORMALIZERS
from tests.conftest import ORIGIN, Env, login, unique_email
from tests.test_webhook_ingest import Recorder

SECRET = "b3f1a9c2-7b4d-4a8e-9f01-2c6d5b8a4e37"
HOTTOK = "hottok-super-secreto-123456"
EMAIL = "maria.souza@gmail.com"
PHONE = "11987654321"


def order(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"o-{uuid.uuid4().hex[:10]}",
        "status": "paid",
        "offer_type": "main",
        "checkoutUrl": "https://pay.cakto.com.br/a8BcHrY?callback=tokensecreto123",
        "amount": 97.0,
        "customer": {
            "name": "Maria Souza",
            "email": EMAIL,
            "phone": PHONE,
            "docNumber": "12345678909",
        },
        "product": {"id": "prod-1", "name": "Curso X"},
        "offer": {"id": "a8BcHrY", "price": 97.0},
        "pix": {"qrCode": "00020126580014br.gov.bcb.pix0136maria@gmail.com5204000053039865406"},
        "createdAt": "2026-10-04T10:00:00-03:00",
    }
    return base | over


def cakto(event: str = "purchase_approved", **over: Any) -> dict[str, Any]:
    return {"secret": SECRET, "event": event, "data": order(**over)}


def sign(raw: bytes, key: str = SECRET) -> dict[str, str]:
    ts = str(int(time.time()))
    mac = hmac.new(key.encode(), ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return {"X-Cakto-Timestamp": ts, "X-Cakto-Signature": f"v1={mac}"}


def app_client(env: Env, db: Database, **cfg: Any) -> TestClient:
    settings = Settings(**{**env.settings().model_dump(), **cfg})
    app = create_app(settings, db=db, box=env.box, event_handler=Recorder())
    return TestClient(app, headers={"Origin": ORIGIN})


@pytest.fixture
def on(env: Env, db: Database) -> Iterator[TestClient]:
    with app_client(env, db, capture_events=True, platform_cakto_secret=SECRET) as c:
        yield c


@pytest.fixture
def off(env: Env, db: Database) -> Iterator[TestClient]:
    with app_client(env, db, platform_cakto_secret=SECRET) as c:
        yield c


def connect(
    c: TestClient, env: Env, provider: str = "cakto", role: str = "owner"
) -> tuple[str, str]:
    email = unique_email("cap")
    tid = env.tenant("Loja Captura", email, role=role)
    assert login(c, email).status_code == 200
    values = {"webhook_secret": SECRET} if provider == "cakto" else {"hottok": HOTTOK}
    res = c.put(f"/v1/connections/{provider}", json={"values": values})
    assert res.status_code == 200, res.text
    return tid, res.json()["webhook_url"].rsplit("/", 1)[1]


def captures(env: Env, tid: str | None = None) -> list[tuple[Any, ...]]:
    return env.sql(
        "SELECT provider, event_type, auth_method FROM event_captures "
        "WHERE tenant_id IS NOT DISTINCT FROM %s ORDER BY captured_at",
        (tid,),
    )


# ------------------------------------------------------------- desligada por padrão


def test_capture_is_off_by_default_and_event_still_works(off: TestClient, env: Env) -> None:
    tid, pid = connect(off, env)
    res = off.post(f"/v1/webhooks/cakto/{pid}", json=cakto())
    assert res.status_code == 200 and res.json() == {"status": "accepted"}
    assert captures(env, tid) == []
    assert get_settings.__wrapped__().capture_events is False  # padrão do código


def test_capture_enabled_records_only_authentic_events(on: TestClient, env: Env) -> None:
    tid, pid = connect(on, env)
    bad = {**cakto(), "secret": "errado"}
    assert on.post(f"/v1/webhooks/cakto/{pid}", json=bad).status_code == 401
    assert captures(env, tid) == []  # prova de origem falhou: nada é guardado
    assert on.post(f"/v1/webhooks/cakto/{pid}", json=cakto()).status_code == 200
    assert captures(env, tid) == [("cakto", "purchase_approved", "segredo_no_corpo")]


def test_unknown_and_ignored_events_are_captured_because_they_teach_the_format(
    on: TestClient, env: Env
) -> None:
    tid, pid = connect(on, env)
    assert (
        on.post(f"/v1/webhooks/cakto/{pid}", json=cakto("evento_novo_da_cakto")).status_code == 200
    )
    assert captures(env, tid) == [("cakto", "evento_novo_da_cakto", "segredo_no_corpo")]


def test_capture_failure_never_breaks_the_webhook(
    on: TestClient, env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid, pid = connect(on, env)

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("falha de captura")

    monkeypatch.setattr(capture, "record", boom)
    res = on.post(f"/v1/webhooks/cakto/{pid}", json=cakto())
    assert res.status_code == 200 and res.json() == {"status": "accepted"}
    assert captures(env, tid) == []


# ------------------------------------------------------------- como provou a origem


def test_auth_method_tells_which_proof_worked(env: Env) -> None:
    raw = json.dumps({"event": "x"}).encode()
    good = sign(raw)
    assert auth_method("cakto", good, {"event": "x"}, SECRET, raw) == "assinatura_hmac"
    assert auth_method("cakto", {}, {"secret": SECRET}, SECRET, raw) == "segredo_no_corpo"
    assert auth_method("cakto", sign(raw, "outra"), {"event": "x"}, SECRET, raw) is None
    assert auth_method("hotmart", {"X-Hotmart-Hottok": HOTTOK}, {}, HOTTOK) == "hottok_cabecalho"
    assert auth_method("hotmart", {}, {"hottok": HOTTOK}, HOTTOK) == "hottok_corpo"
    assert auth_method("hotmart", {}, {"hottok": "x"}, HOTTOK) is None
    assert auth_method("cakto", {}, {"secret": SECRET}, "") is None
    assert auth_method("outro", {}, {}, SECRET) is None


def test_signature_only_delivery_is_recorded_as_hmac(on: TestClient, env: Env) -> None:
    tid, pid = connect(on, env)
    body = {"event": "purchase_approved", "data": order()}  # sem `secret` no corpo
    raw = json.dumps(body).encode()
    res = on.post(
        f"/v1/webhooks/cakto/{pid}",
        content=raw,
        headers={"Content-Type": "application/json", **sign(raw)},
    )
    assert res.status_code == 200
    assert captures(env, tid) == [("cakto", "purchase_approved", "assinatura_hmac")]


def test_hotmart_header_and_body_proofs_are_recorded(on: TestClient, env: Env) -> None:
    tid, pid = connect(on, env, "hotmart")
    body = {"event": "PURCHASE_APPROVED", "data": {"buyer": {"email": EMAIL}}}
    res = on.post(f"/v1/webhooks/hotmart/{pid}", json=body, headers={"X-Hotmart-Hottok": HOTTOK})
    assert res.status_code == 200
    res = on.post(f"/v1/webhooks/hotmart/{pid}", json={**body, "hottok": HOTTOK, "id": "2"})
    assert res.status_code == 200
    assert [c[2] for c in captures(env, tid)] == ["hottok_cabecalho", "hottok_corpo"]


# -------------------------------------------------------------- segredos e cifragem


def test_secrets_are_redacted_before_storing_and_never_readable(
    on: TestClient, env: Env, db: Database
) -> None:
    tid, pid = connect(on, env)
    body = cakto()
    body["data"]["api_token"] = "token-da-api-999"
    raw = json.dumps(body).encode()
    res = on.post(
        f"/v1/webhooks/cakto/{pid}",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer bearer-secreto",
            "Cookie": "sessao=abc",
            "X-Hotmart-Hottok": HOTTOK,
            "X-Cakto-Signature": "v1=abc123",
            "User-Agent": "Cakto/1.0",
            **sign(raw),
        },
    )
    assert res.status_code == 200
    cap_id = env.sql("SELECT id FROM event_captures WHERE tenant_id = %s", (tid,))[0][0]
    with db.tx(tenant_id=uuid.UUID(tid)) as conn:
        found = capture.load_capture(conn, env.box, uuid.UUID(str(cap_id)))
    assert found is not None
    text = json.dumps(found["payload"]) + json.dumps(found["headers"])
    for secret in (SECRET, HOTTOK, "token-da-api-999", "bearer-secreto", "sessao=abc"):
        assert secret not in text
    assert found["payload"]["secret"] == capture.REDACTED
    assert found["payload"]["data"]["api_token"] == capture.REDACTED
    assert set(found["headers"]) >= {"content-type", "user-agent", "x-cakto-signature"}
    assert not {"authorization", "cookie", "x-hotmart-hottok"} & set(found["headers"])
    blob = bytes(
        env.sql("SELECT payload_encrypted FROM event_captures WHERE tenant_id = %s", (tid,))[0][0]
    )
    assert EMAIL.encode() not in blob and b"Maria" not in blob  # cifrado em repouso


def test_redact_is_recursive_and_keeps_empty_values() -> None:
    out = capture.redact({"a": {"Token": "x", "list": [{"hottok": "y"}], "ok": "v"}, "secret": ""})
    assert out == {
        "a": {"Token": "[redigido]", "list": [{"hottok": "[redigido]"}], "ok": "v"},
        "secret": "",
    }


# ---------------------------------------------------------------- máscara e anonimização


def test_mask_hides_personal_data_but_keeps_structure_and_types() -> None:
    body = cakto()["data"]
    masked = capture.mask({"event": "x", "data": body})
    text = json.dumps(masked, ensure_ascii=False)
    for pii in (EMAIL, PHONE, "Maria", "Souza", "12345678909", "tokensecreto123", "maria@gmail"):
        assert pii not in text, pii
    assert masked["data"]["customer"]["name"] == "M••• S•••"
    assert masked["data"]["customer"]["email"] == "m•••@gmail.com"
    assert masked["data"]["customer"]["phone"] == "••••4321"
    assert masked["data"]["customer"]["docNumber"] == capture.HIDDEN
    assert masked["data"]["checkoutUrl"] == "https://pay.cakto.com.br/a8BcHrY?…"
    assert masked["data"]["pix"]["qrCode"] == "[código ocultado]"
    assert masked["data"]["amount"] == 97.0 and masked["data"]["product"]["id"] == "prod-1"
    assert compare.leaf_paths(masked) == compare.leaf_paths({"event": "x", "data": body})


def test_mask_catches_emails_and_long_numbers_in_free_text() -> None:
    out = capture.mask({"note": "fale com ana@x.com ou 11987654321 já"})
    assert "ana@x.com" not in out["note"] and "11987654321" not in out["note"]


def test_anonymize_keeps_the_event_usable_without_real_data() -> None:
    body = cakto(id="ped-abcdef1234")  # fixo: id só de dígitos longos seria zerado (ver anonymize)
    anon = capture.anonymize(body)
    text = json.dumps(anon, ensure_ascii=False)
    for pii in (EMAIL, PHONE, "Maria", "12345678909", "tokensecreto123", SECRET):
        assert pii not in text, pii
    event = NORMALIZERS["cakto"](anon)
    assert event is not None and event.email == "cliente.exemplo@example.test"
    assert event.phone == "5511999990001" and event.amount_cents == 9700
    assert anon["data"]["id"] == body["data"]["id"]  # id de pedido pode ficar


# ------------------------------------------------------------------ validade e limite


def test_ttl_expiry_and_purge(env: Env, db: Database) -> None:
    now = datetime.now(UTC)
    cfg = CaptureConfig(enabled=True, ttl_hours=2, keep=100)
    for _ in range(2):
        capture.safe_record(
            db,
            env.box,
            cfg,
            tenant_id=None,
            provider="cakto",
            headers={},
            body=cakto("ttl"),
            auth_method="x",
        )
    rows = env.sql("SELECT expires_at, captured_at FROM event_captures WHERE event_type = 'ttl'")
    assert all(r[0] - r[1] == timedelta(hours=2) for r in rows) and len(rows) == 2
    env.sql(
        "UPDATE event_captures SET expires_at = now() - interval '1 hour' WHERE id = "
        "(SELECT id FROM event_captures WHERE event_type = 'ttl' LIMIT 1)"
    )
    assert capture.purge_expired(db, now=now) >= 1
    assert env.sql("SELECT count(*) FROM event_captures WHERE event_type = 'ttl'") == [(1,)]
    env.sql("DELETE FROM event_captures WHERE event_type = 'ttl'")


def test_keep_limit_trims_the_oldest(env: Env, db: Database) -> None:
    tid = env.tenant("Loja Cap Max", unique_email("max"))
    cfg = CaptureConfig(enabled=True, ttl_hours=72, keep=3)
    for i in range(6):
        capture.safe_record(
            db,
            env.box,
            cfg,
            tenant_id=uuid.UUID(tid),
            provider="hotmart",
            headers={},
            body={"event": f"E{i}"},
            auth_method=None,
        )
    kept = [r[1] for r in captures(env, tid)]
    assert kept == ["E3", "E4", "E5"]


def test_disabled_config_records_nothing(env: Env, db: Database) -> None:
    capture.safe_record(
        db,
        env.box,
        CaptureConfig(),
        tenant_id=None,
        provider="cakto",
        headers={},
        body={"event": "nada-off"},
        auth_method=None,
    )
    assert env.sql("SELECT count(*) FROM event_captures WHERE event_type = 'nada-off'") == [(0,)]


# ------------------------------------------------------------------- plataforma e RLS


def test_platform_purchases_are_captured_with_no_tenant_and_hidden_from_customers(
    on: TestClient, env: Env, db: Database
) -> None:
    body = {"secret": SECRET, "event": "purchase_approved", "data": order(id="plat-1")}
    res = on.post("/v1/platform/webhooks/cakto", json=body)
    assert res.status_code == 200
    rows = env.sql(
        "SELECT provider, event_type FROM event_captures WHERE tenant_id IS NULL "
        "AND event_type = 'purchase_approved'"
    )
    assert ("cakto", "purchase_approved") in rows
    tid = env.tenant("Loja Curiosa", unique_email("cur"))
    with db.tx(tenant_id=uuid.UUID(tid)) as conn:
        seen = conn.execute("SELECT count(*) AS n FROM event_captures").fetchone()
    assert seen is not None and seen["n"] == 0  # cliente nunca vê captura da plataforma


def test_each_customer_sees_only_its_own_captures_and_only_owner_or_admin(
    on: TestClient, env: Env
) -> None:
    a_tid, a_pid = connect(on, env)
    on.post(f"/v1/webhooks/cakto/{a_pid}", json=cakto("evento_a"))
    cap_a = on.get("/v1/captures").json()["items"][0]["id"]
    b_tid, b_pid = connect(on, env)  # login de outro cliente
    on.post(f"/v1/webhooks/cakto/{b_pid}", json=cakto("evento_b"))
    listing = on.get("/v1/captures").json()
    assert [i["event_type"] for i in listing["items"]] == ["evento_b"]
    assert on.get(f"/v1/captures/{cap_a}").status_code == 404
    assert a_tid != b_tid
    agent_email = unique_email("ag")
    env.tenant("Loja Agente", agent_email, role="agent")
    assert login(on, agent_email).status_code == 200
    assert on.get("/v1/captures").status_code == 403


def test_capture_api_shows_masked_payload_and_the_comparison(on: TestClient, env: Env) -> None:
    _, pid = connect(on, env)
    on.post(f"/v1/webhooks/cakto/{pid}", json=cakto())
    on.post(f"/v1/webhooks/cakto/{pid}", json=cakto("evento_novo"))
    listing = on.get("/v1/captures").json()
    assert listing["enabled"] is True and len(listing["items"]) == 2
    first = next(i for i in listing["items"] if i["event_type"] == "purchase_approved")
    detail = on.get(f"/v1/captures/{first['id']}").json()
    text = json.dumps(detail, ensure_ascii=False)
    for secret in (EMAIL, PHONE, "Maria", SECRET, "tokensecreto123", "12345678909"):
        assert secret not in text, secret
    assert detail["auth_method"] == "segredo_no_corpo"
    assert detail["comparison"]["known_event"] is True and detail["comparison"]["problems"] == []
    other = next(i for i in listing["items"] if i["event_type"] == "evento_novo")
    unknown = on.get(f"/v1/captures/{other['id']}").json()["comparison"]
    assert unknown["known_event"] is False and "desconhecido" in unknown["problems"][0].lower()


def test_capture_api_reports_when_capture_is_off(off: TestClient, env: Env) -> None:
    connect(off, env)
    listing = off.get("/v1/captures").json()
    assert listing["enabled"] is False and listing["items"] == []
    assert off.get(f"/v1/captures/{uuid.uuid4()}").status_code == 404


def test_capture_api_requires_login(off: TestClient) -> None:
    off.cookies.clear()
    assert off.get("/v1/captures").status_code in (401, 403)


# --------------------------------------------------------------------------- conferência


def test_compare_cakto_example_finds_every_field() -> None:
    report = compare.compare("cakto", cakto())
    assert report["known_event"] and report["kind"] == "purchase_approved" and report["normalized"]
    fields = {f["field"]: f for f in report["fields"]}
    assert all(f["status"] == "ok" for f in fields.values())
    assert (
        fields["email"]["found_at"] == "data.customer.email"
        and fields["amount"]["type"] == "número"
    )
    assert report["problems"] == []
    extras = {e["path"]: e["type"] for e in report["extras"]}
    assert extras["data.status"] == "texto" and "data.pix.qrCode" in extras
    assert "data.customer.email" not in extras and "secret" not in extras
    assert "value" not in json.dumps(report["extras"])
    assert EMAIL not in json.dumps(report)  # só caminhos e tipos, nunca valores


def test_compare_reports_missing_fields_and_what_it_tried() -> None:
    body = cakto("pix_gerado", customer={"name": "Sem Contato"}, checkoutUrl="")
    report = compare.compare("cakto", body)
    fields = {f["field"]: f for f in report["fields"]}
    assert (
        fields["email"]["status"] == "ausente" and "data.customer.email" in fields["email"]["tried"]
    )
    problems = " ".join(report["problems"])
    assert "Sem e-mail e sem telefone" in problems and "Sem link de pagamento" in problems


def test_compare_boleto_uses_the_boleto_url_and_v2_list_uses_the_main_order() -> None:
    boleto = cakto("boleto_gerado", boleto={"boletoUrl": "https://boleto.test/abc"}, checkoutUrl="")
    url = next(f for f in compare.compare("cakto", boleto)["fields"] if f["field"] == "url")
    assert url["found_at"] == "data.boleto.boletoUrl"
    v2 = {"event": "purchase_approved", "data": [order(offer_type="orderbump"), order(id="main-1")]}
    assert compare.compare("cakto", v2)["fields"][0]["status"] == "ok"


def test_compare_hotmart_guesses_and_unknown_event() -> None:
    body = {
        "event": "PURCHASE_APPROVED",
        "data": {
            "buyer": {"email": "x@y.test"},
            "product": {"id": 1},
            "purchase": {"transaction": "T1", "price": {"value": 10}},
        },
    }
    report = compare.compare("hotmart", body)
    assert report["known_event"] and any("telefone" in p for p in report["problems"]) is False
    assert compare.compare("hotmart", {"event": "PURCHASE_NEW_THING"})["known_event"] is False
    text = compare.render(compare.compare("hotmart", {"event": "PURCHASE_NEW_THING"}))
    assert "DESCONHECIDO" in text and "Problemas" in text


def test_render_lists_found_missing_and_extras_without_values() -> None:
    text = compare.render(compare.compare("cakto", cakto()))
    assert "[ok] e-mail do cliente: em data.customer.email (texto)" in text
    assert EMAIL not in text and "Campos que o evento traz e ninguém lê" in text


# --------------------------------------------------------------------------------- CLI


@pytest.fixture
def cli_env(env: Env, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    monkeypatch.setenv("FM_ENV", "dev")
    monkeypatch.setenv("FM_DATABASE_ADMIN_URL", env.admin_url)
    monkeypatch.setenv("FM_DATABASE_URL", env.app_url)
    monkeypatch.setenv("FM_SECRETS_KEYS", env.key_spec)
    get_settings.cache_clear()
    yield env
    get_settings.cache_clear()


def test_cli_list_show_export_and_purge(
    cli_env: Env, db: Database, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = CaptureConfig(enabled=True, ttl_hours=72, keep=100)
    capture.safe_record(
        db,
        cli_env.box,
        cfg,
        tenant_id=None,
        provider="cakto",
        headers={"user-agent": "t"},
        body=cakto("purchase_approved", id="cli-1"),
        auth_method="segredo_no_corpo",
    )
    cap_id = cli_env.sql(
        "SELECT id FROM event_captures WHERE payload_encrypted IS NOT NULL "
        "AND event_type = 'purchase_approved' AND tenant_id IS NULL "
        "ORDER BY captured_at DESC LIMIT 1"
    )[0][0]
    assert cli.main(["capture", "list", "--provider", "cakto"]) == 0
    assert str(cap_id) in capsys.readouterr().out
    assert cli.main(["capture", "show", str(cap_id)]) == 0
    shown = capsys.readouterr().out
    assert "segredo_no_corpo" in shown and "[ok] e-mail do cliente" in shown
    for pii in (EMAIL, PHONE, "Maria", SECRET, "12345678909"):
        assert pii not in shown, pii
    out = tmp_path / "fx.json"
    assert cli.main(["capture", "export", str(cap_id), "--out", str(out)]) == 0
    fixture = json.loads(out.read_text(encoding="utf-8"))
    assert fixture["origem"] == "captura_anonimizada" and fixture["provider"] == "cakto"
    raw = out.read_text(encoding="utf-8")
    for pii in (EMAIL, PHONE, "Maria", SECRET, "12345678909", "tokensecreto123"):
        assert pii not in raw, pii
    assert NORMALIZERS["cakto"](fixture["payload"]) is not None
    assert cli.main(["capture", "export", str(cap_id), "--out", str(out)]) == 1  # não sobrescreve
    assert cli.main(["capture", "show", str(uuid.uuid4())]) == 1
    cli_env.sql(
        "UPDATE event_captures SET expires_at = now() - interval '1 minute' WHERE id = %s",
        (str(cap_id),),
    )
    capsys.readouterr()
    assert cli.main(["capture", "purge"]) == 0
    assert "apagadas" in capsys.readouterr().out
    assert cli_env.sql("SELECT count(*) FROM event_captures WHERE id = %s", (str(cap_id),)) == [
        (0,)
    ]
