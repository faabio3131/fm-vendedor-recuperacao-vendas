"""Bloco 16: primeiros passos. O estado vem do que existe de verdade, e ligar o envio é barrado."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import Env, connect_whatsapp, login, unique_email

OFFER = {
    "name": "Curso Z",
    "description": "Aprenda Z",
    "price_cents": 19700,
    "payment_url": "https://pay.example.test/z",
}


def _owner(client: TestClient, env: Env, name: str = "Loja Primeiros Passos") -> str:
    email = unique_email("onb")
    tid = env.tenant(name, email)
    assert login(client, email).status_code == 200
    return tid


def _steps(client: TestClient) -> dict[str, Any]:
    res = client.get("/v1/onboarding")
    assert res.status_code == 200, res.text
    return {s["key"]: s for s in res.json()["steps"]}


def test_new_client_starts_with_nothing_done_and_both_switches_blocked(
    client: TestClient, env: Env
) -> None:
    _owner(client, env)
    body = client.get("/v1/onboarding").json()
    assert body["done"] == 0 and body["percent"] == 0 and body["total"] == len(body["steps"])
    assert body["can_enable"]["recuperacao"] == {
        "allowed": False,
        "missing": ["whatsapp", "consentimento"],
    }
    assert body["can_enable"]["ia"] == {"allowed": False, "missing": ["ofertas"]}
    for step in body["steps"]:
        assert step["todo"] and step["href"].startswith("/") and step["action"]


def test_each_step_turns_done_from_real_state(client: TestClient, env: Env) -> None:
    tid = _owner(client, env)
    connect_whatsapp(client, env, tid)
    assert _steps(client)["whatsapp"]["done"] is True
    assert client.post("/v1/seller/offers", json=OFFER).status_code == 200
    assert _steps(client)["ofertas"]["done"] is True
    assert (
        client.put(
            "/v1/recovery/settings", json={"consent_declared": True, "recovery_enabled": True}
        ).status_code
        == 200
    )
    steps = _steps(client)
    assert steps["consentimento"]["done"] and steps["recuperacao"]["done"]
    assert steps["templates"]["done"] is False and steps["persona"]["done"] is False
    client.put("/v1/recovery/templates/carrinho_1", json={"body": "Oi {nome}, vi seu carrinho!"})
    client.post("/v1/recovery/templates/carrinho_1/status", json={"status": "approved"})
    assert (
        client.put(
            "/v1/seller/settings", json={"ai_enabled": True, "ai_persona": "Simpático e direto"}
        ).status_code
        == 200
    )
    steps = _steps(client)
    assert steps["templates"]["done"] and steps["persona"]["done"] and steps["ia"]["done"]
    body = client.get("/v1/onboarding").json()
    assert body["can_enable"]["recuperacao"]["allowed"] and body["can_enable"]["ia"]["allowed"]
    assert body["done"] == 7 and body["total"] == 8  # só a plataforma de vendas é opcional


def test_pending_connection_does_not_count_as_connected(client: TestClient, env: Env) -> None:
    _owner(client, env)
    res = client.put(
        "/v1/connections/whatsapp_cloud",
        json={
            "values": {
                "phone_number_id": "1055550001",
                "waba_id": "2077770002",
                "access_token": "EAAG" + "y" * 40,
            }
        },
    )
    assert res.status_code == 200
    assert _steps(client)["whatsapp"]["done"] is False  # salvar não é o mesmo que testar


def test_recovery_cannot_be_turned_on_without_a_connected_whatsapp(
    client: TestClient, env: Env
) -> None:
    tid = _owner(client, env)
    res = client.put(
        "/v1/recovery/settings", json={"consent_declared": True, "recovery_enabled": True}
    )
    assert res.status_code == 400 and res.json()["error"]["code"] == "setup_incomplete"
    assert "WhatsApp" in res.json()["error"]["message"]
    assert client.get("/v1/recovery/settings").json()["recovery_enabled"] is False
    connect_whatsapp(client, env, tid)
    ok = client.put(
        "/v1/recovery/settings", json={"consent_declared": True, "recovery_enabled": True}
    )
    assert ok.status_code == 200 and ok.json()["recovery_enabled"] is True


def test_consent_message_still_comes_first(client: TestClient, env: Env) -> None:
    _owner(client, env)
    res = client.put("/v1/recovery/settings", json={"recovery_enabled": True})
    assert res.status_code == 400 and res.json()["error"]["code"] == "consent_required"


def test_ai_cannot_be_turned_on_without_an_active_offer(client: TestClient, env: Env) -> None:
    _owner(client, env)
    res = client.put("/v1/seller/settings", json={"ai_enabled": True})
    assert res.status_code == 400 and res.json()["error"]["code"] == "setup_incomplete"
    assert client.get("/v1/seller/settings").json()["ai_enabled"] is False
    offer = client.post("/v1/seller/offers", json=OFFER).json()
    client.put(f"/v1/seller/offers/{offer['id']}", json={**OFFER, "active": False})
    assert client.put("/v1/seller/settings", json={"ai_enabled": True}).status_code == 400
    client.put(f"/v1/seller/offers/{offer['id']}", json={**OFFER, "active": True})
    assert client.put("/v1/seller/settings", json={"ai_enabled": True}).status_code == 200


def test_turning_off_and_tweaking_settings_is_never_blocked(client: TestClient, env: Env) -> None:
    tid = _owner(client, env)
    connect_whatsapp(client, env, tid)
    client.post("/v1/seller/offers", json=OFFER)
    on = {"consent_declared": True, "recovery_enabled": True}
    assert client.put("/v1/recovery/settings", json=on).status_code == 200
    assert client.put("/v1/seller/settings", json={"ai_enabled": True}).status_code == 200
    # A conexão cai depois: quem já está ligado pode ajustar e desligar sem ser barrado.
    env.sql("UPDATE connections SET status = 'needs_attention' WHERE tenant_id = %s", (tid,))
    env.sql("DELETE FROM offers WHERE tenant_id = %s", (tid,))
    assert client.put("/v1/recovery/settings", json={"daily_cap": 2}).status_code == 200
    assert (
        client.put(
            "/v1/seller/settings", json={"ai_enabled": True, "ai_persona": "Calmo"}
        ).status_code
        == 200
    )
    assert client.put("/v1/recovery/settings", json={"recovery_enabled": False}).status_code == 200
    assert client.put("/v1/seller/settings", json={"ai_enabled": False}).status_code == 200


def test_another_clients_data_never_counts(client: TestClient, env: Env) -> None:
    other = _owner(client, env, "Loja Outro")
    connect_whatsapp(client, env, other)
    client.post("/v1/seller/offers", json=OFFER)
    client.post("/v1/auth/logout")
    _owner(client, env, "Loja Vazia")
    body = client.get("/v1/onboarding").json()
    assert body["done"] == 0
    assert body["can_enable"]["ia"]["allowed"] is False


def test_any_role_reads_but_login_is_required(client: TestClient, env: Env) -> None:
    assert client.get("/v1/onboarding").status_code == 401
    tid = _owner(client, env)
    agent = unique_email("agent")
    env.invite(tid, agent, "agent")
    client.post("/v1/auth/logout")
    assert login(client, agent).status_code == 200
    assert client.get("/v1/onboarding").status_code == 200


def test_response_never_carries_credentials(client: TestClient, env: Env) -> None:
    tid = _owner(client, env)
    connect_whatsapp(client, env, tid)
    text = client.get("/v1/onboarding").text
    assert "EAAG" not in text and "1055550001" not in text and "2077770002" not in text
