from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from fm_seller.providers.catalog import PROVIDERS
from tests.conftest import Env, login, unique_email

WA = {
    "phone_number_id": "1055550001",
    "waba_id": "2077770002",
    "access_token": "EAAGsegredo1234567890",
}


def _owner(client: TestClient, env: Env, plan: str = "fase-1") -> tuple[str, str]:
    email = unique_email("owner")
    tenant_id = env.tenant("Loja Conexoes", email, plan=plan)
    assert login(client, email).status_code == 200
    return tenant_id, email


def test_every_catalog_feature_exists_in_top_plan(env: Env) -> None:
    features = set(env.sql("SELECT unnest(features) FROM plans WHERE key = 'fase-4'")[0:0] or [])
    rows = env.sql("SELECT unnest(features) FROM plans WHERE key = 'fase-4'")
    features = {r[0] for r in rows}
    assert {p.feature for p in PROVIDERS} <= features


def test_secret_never_returned_and_never_stored_in_clear(client: TestClient, env: Env) -> None:
    tenant_id, _ = _owner(client, env)
    res = client.put("/v1/connections/whatsapp_cloud", json={"values": WA})
    assert res.status_code == 200, res.text
    body = res.json()
    assert "EAAGsegredo1234567890" not in json.dumps(body)
    assert body["values"]["access_token"] == "••••7890"
    assert body["values"]["phone_number_id"] == "1055550001"
    assert body["status"] == "pending"
    assert body["webhook_url"].startswith("https://api.example.test/v1/webhooks/whatsapp_cloud/")
    assert body["webhook_secret_once"]

    listing = client.get("/v1/connections").text
    assert "EAAGsegredo1234567890" not in listing
    assert body["webhook_secret_once"] not in listing

    blob = bytes(
        env.sql("SELECT config_encrypted FROM connections WHERE tenant_id = %s", (tenant_id,))[0][0]
    )
    assert b"EAAGsegredo1234567890" not in blob
    hint = env.sql("SELECT config_hint::text FROM connections WHERE tenant_id = %s", (tenant_id,))[
        0
    ][0]
    assert "EAAGsegredo1234567890" not in hint and body["webhook_secret_once"] not in hint


def test_blank_secret_keeps_current_and_webhook_secret_is_shown_once(
    client: TestClient, env: Env
) -> None:
    tenant_id, _ = _owner(client, env)
    first = client.put("/v1/connections/whatsapp_cloud", json={"values": WA}).json()
    second = client.put(
        "/v1/connections/whatsapp_cloud",
        json={"values": {"phone_number_id": "1055550009", "access_token": ""}},
    ).json()
    assert "webhook_secret_once" not in second
    assert second["webhook_url"] == first["webhook_url"]
    assert second["values"]["phone_number_id"] == "1055550009"
    assert second["values"]["access_token"] == "••••7890"
    blob = bytes(
        env.sql("SELECT config_encrypted FROM connections WHERE tenant_id = %s", (tenant_id,))[0][0]
    )
    stored: dict[str, Any] = env.box.decrypt(blob, tenant_id=tenant_id, provider="whatsapp_cloud")
    assert stored["access_token"] == "EAAGsegredo1234567890"
    assert stored["webhook_secret"] == first["webhook_secret_once"]


def test_missing_and_unknown_fields_are_rejected(client: TestClient, env: Env) -> None:
    _owner(client, env)
    res = client.put("/v1/connections/whatsapp_cloud", json={"values": {"phone_number_id": "1"}})
    assert res.status_code == 400 and res.json()["error"]["code"] == "missing_fields"
    res = client.put("/v1/connections/whatsapp_cloud", json={"values": {**WA, "extra": "x"}})
    assert res.status_code == 400 and res.json()["error"]["code"] == "unknown_field"
    assert client.put("/v1/connections/inexistente", json={"values": {}}).status_code == 404


def test_test_endpoint_marks_connected_in_test_env(client: TestClient, env: Env) -> None:
    _owner(client, env)
    client.put("/v1/connections/whatsapp_cloud", json={"values": WA})
    res = client.post("/v1/connections/whatsapp_cloud/test").json()
    assert res["status"] == "connected" and res["last_verified_at"]
    assert client.post("/v1/connections/hotmart/test").status_code == 404


def test_plan_gates_providers(client: TestClient, env: Env) -> None:
    _owner(client, env, plan="fase-1")
    res = client.put(
        "/v1/connections/meta_ads",
        json={"values": {"ad_account_id": "1", "access_token": "x" * 20}},
    )
    assert res.status_code == 402 and res.json()["error"]["code"] == "plan_required"


def test_higher_plan_unlocks_provider(client: TestClient, env: Env) -> None:
    _owner(client, env, plan="fase-3")
    res = client.put(
        "/v1/connections/meta_ads",
        json={"values": {"ad_account_id": "1", "access_token": "x" * 20}},
    )
    assert res.status_code == 200


def test_agent_role_cannot_change_connections(client: TestClient, env: Env) -> None:
    tenant_id, _ = _owner(client, env)
    agent = unique_email("agente")
    env.invite(tenant_id, agent, "agent")
    client.cookies.clear()
    assert login(client, agent).status_code == 200
    assert client.put("/v1/connections/cakto", json={"values": {}}).status_code == 403
    assert client.delete("/v1/connections/cakto").status_code == 403
    assert client.get("/v1/connections").status_code == 200


def test_tenants_cannot_see_each_others_connections(client: TestClient, env: Env) -> None:
    _owner(client, env)
    client.put("/v1/connections/whatsapp_cloud", json={"values": WA})
    client.cookies.clear()
    _owner(client, env)
    assert client.get("/v1/connections").json() == []
    assert client.post("/v1/connections/whatsapp_cloud/test").status_code == 404


def test_delete_and_audit_without_secrets(client: TestClient, env: Env) -> None:
    tenant_id, _ = _owner(client, env)
    client.put("/v1/connections/whatsapp_cloud", json={"values": WA})
    assert client.delete("/v1/connections/whatsapp_cloud").status_code == 200
    assert client.delete("/v1/connections/whatsapp_cloud").status_code == 404
    rows = env.sql(
        "SELECT action, detail::text FROM audit_log WHERE tenant_id = %s ORDER BY id", (tenant_id,)
    )
    actions = [r[0] for r in rows]
    assert actions[-2:] == ["connection.saved", "connection.removed"]
    assert "EAAGsegredo1234567890" not in json.dumps(rows)


def test_hotmart_secret_field_masking(client: TestClient, env: Env) -> None:
    _owner(client, env)
    body = client.put(
        "/v1/connections/hotmart", json={"values": {"hottok": "hottok-valor-bem-longo-9876"}}
    ).json()
    assert body["values"]["hottok"] == "••••9876"


def test_migrations_are_idempotent(env: Env) -> None:
    from fm_seller.migrate import apply_migrations

    assert apply_migrations(env.admin_url) == []
