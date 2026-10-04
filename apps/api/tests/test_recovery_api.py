from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from fm_seller.db import Database
from fm_seller.events import normalize as n
from fm_seller.events.normalize import CheckoutEvent
from fm_seller.recovery.engine import handle_event
from tests.conftest import Env, connect_whatsapp, login, unique_email


def _owner(client: TestClient, env: Env, plan: str = "fase-1") -> tuple[str, str]:
    email = unique_email("rapi")
    tid = env.tenant("Loja API Recuperacao", email, plan=plan)
    assert login(client, email).status_code == 200
    return tid, email


def _enable(client: TestClient, env: Env, tid: str) -> None:
    connect_whatsapp(client, env, tid)
    res = client.put(
        "/v1/recovery/settings", json={"consent_declared": True, "recovery_enabled": True}
    )
    assert res.status_code == 200, res.text


def test_settings_default_off_and_consent_gate(client: TestClient, env: Env) -> None:
    tid, _ = _owner(client, env)
    s = client.get("/v1/recovery/settings").json()
    assert s["recovery_enabled"] is False and s["consent_declared"] is False
    bad = client.put("/v1/recovery/settings", json={"recovery_enabled": True})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "consent_required"
    _enable(client, env, tid)
    on = client.get("/v1/recovery/settings").json()
    assert on["recovery_enabled"] is True and on["consent_declared_at"] is not None
    off = client.put("/v1/recovery/settings", json={"consent_declared": False}).json()
    assert off["recovery_enabled"] is False and off["consent_declared"] is False


def test_settings_validation(client: TestClient, env: Env) -> None:
    _owner(client, env)
    assert client.put("/v1/recovery/settings", json={"timezone": "Marte/Olimpo"}).status_code == 400
    assert client.put("/v1/recovery/settings", json={"daily_cap": 99}).status_code == 422
    ok = client.put("/v1/recovery/settings", json={"timezone": "America/Manaus", "daily_cap": 2})
    assert ok.status_code == 200 and ok.json()["timezone"] == "America/Manaus"


def test_sequences_default_edit_validate_reset(client: TestClient, env: Env) -> None:
    _owner(client, env)
    seqs = {s["trigger"]: s for s in client.get("/v1/recovery/sequences").json()}
    assert set(seqs) == set(n.RECOVERY_TRIGGERS) and seqs["pix_pending"]["is_default"]
    steps = [{"delay_minutes": 20, "template_key": "meu_1"}]
    ok = client.put("/v1/recovery/sequences/pix_pending", json={"enabled": True, "steps": steps})
    assert ok.status_code == 200
    assert client.get("/v1/recovery/sequences").json()[1]["is_default"] is False
    for bad in (
        [],
        [{"delay_minutes": 0, "template_key": "x"}],
        [{"delay_minutes": 10, "template_key": "a"}, {"delay_minutes": 5, "template_key": "b"}],
        [{"delay_minutes": 10}],
    ):
        r = client.put("/v1/recovery/sequences/pix_pending", json={"steps": bad})
        assert r.status_code == 400, bad
    assert client.put("/v1/recovery/sequences/nada", json={"steps": steps}).status_code == 404
    assert client.delete("/v1/recovery/sequences/pix_pending").status_code == 200
    assert all(s["is_default"] for s in client.get("/v1/recovery/sequences").json())


def test_template_flow_edit_resets_approval(client: TestClient, env: Env) -> None:
    _owner(client, env)
    tpls = {t["key"]: t for t in client.get("/v1/recovery/templates").json()}
    assert tpls["carrinho_1"]["saved"] is False and tpls["carrinho_1"]["meta_status"] == "draft"
    assert (
        client.post(
            "/v1/recovery/templates/carrinho_1/status", json={"status": "approved"}
        ).status_code
        == 404
    )
    body = "Oi {nome}, voltou? {link}"
    assert client.put("/v1/recovery/templates/carrinho_1", json={"body": body}).status_code == 200
    ok = client.post("/v1/recovery/templates/carrinho_1/status", json={"status": "approved"})
    assert ok.json()["meta_status"] == "approved"
    same = client.put("/v1/recovery/templates/carrinho_1", json={"body": body}).json()
    assert same["meta_status"] == "approved"  # texto igual mantém aprovação
    changed = client.put("/v1/recovery/templates/carrinho_1", json={"body": body + "!"}).json()
    assert changed["meta_status"] == "draft"  # texto novo precisa de nova aprovação
    assert (
        client.post("/v1/recovery/templates/carrinho_1/status", json={"status": "x"}).status_code
        == 400
    )
    assert client.put("/v1/recovery/templates/Chave Ruim", json={"body": "a"}).status_code == 400
    assert client.put("/v1/recovery/templates/vazio", json={"body": "   "}).status_code == 400


def test_suppression_stops_open_cases_and_masks_identity(
    client: TestClient, env: Env, db: Database
) -> None:
    tid, _ = _owner(client, env)
    _enable(client, env, tid)
    tenant = uuid.UUID(tid)
    event = CheckoutEvent(
        n.ABANDONED_CART, "x", "sup-1", "Ana", "ana@example.test", "5511977776666",
        "p", "Curso", 5000, None,
    )  # fmt: skip
    with db.tx(tenant_id=tenant) as conn:
        handle_event(conn, tenant, event, now=datetime.now(UTC))
    assert client.get("/v1/recovery/cases?status=open").json()[0]["status"] == "open"
    res = client.post("/v1/recovery/suppressions", json={"identity": "(11) 97777-6666"})
    assert res.status_code == 200
    case = client.get("/v1/recovery/cases").json()[0]
    assert case["status"] == "stopped" and case["closed_reason"] == "opt_out"
    listed = client.get("/v1/recovery/suppressions").json()
    assert listed[0]["identity"].endswith("6666") and "97777" not in listed[0]["identity"]
    assert "ana@example.test" not in client.get("/v1/recovery/cases").text
    assert client.post("/v1/recovery/suppressions", json={"identity": "abc"}).status_code == 400


def test_summary_and_readiness(client: TestClient, env: Env, db: Database) -> None:
    tid, _ = _owner(client, env)
    s = client.get("/v1/recovery/summary").json()
    assert s["cases_total"] == 0 and s["readiness"]["whatsapp_connected"] is False
    assert s["readiness"]["recovery_enabled"] is False
    _enable(client, env, tid)
    tenant = uuid.UUID(tid)
    for ref in ("s1", "s2"):
        ev = CheckoutEvent(
            n.PIX_PENDING, "x", ref, "Bia", None, "5521988887777", "p", f"Prod {ref}", 100, None
        )  # fmt: skip
        with db.tx(tenant_id=tenant) as conn:
            handle_event(conn, tenant, ev, now=datetime.now(UTC))
    s = client.get("/v1/recovery/summary").json()
    assert s["cases_total"] == 2 and s["cases_by_status"]["open"] == 2
    assert s["readiness"]["recovery_enabled"] is True and s["readiness"]["consent_declared"]


def test_agent_can_read_but_not_edit(client: TestClient, env: Env) -> None:
    tid, _ = _owner(client, env)
    agent = unique_email("agent")
    env.invite(tid, agent, "agent")
    client.post("/v1/auth/logout")
    assert login(client, agent).status_code == 200
    assert client.get("/v1/recovery/settings").status_code == 200
    assert client.put("/v1/recovery/settings", json={"daily_cap": 2}).status_code == 403
    assert (
        client.post("/v1/recovery/suppressions", json={"identity": "11999990000"}).status_code
        == 403
    )


def test_plan_without_feature_and_isolation(client: TestClient, env: Env) -> None:
    _, email = _owner(client, env)
    env.sql(
        "UPDATE tenant_plans SET status = 'canceled' WHERE tenant_id IN "
        "(SELECT tenant_id FROM pending_invites WHERE lower(email) = lower(%s))",
        (email,),
    )
    r = client.get("/v1/recovery/summary")
    assert r.status_code == 403 and r.json()["error"]["code"] == "plan_required"


def test_requires_login(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/v1/recovery/summary").status_code == 401
