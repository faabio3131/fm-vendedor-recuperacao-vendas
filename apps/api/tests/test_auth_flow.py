from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.conftest import Env, login, sim_token, unique_email


def test_health_and_ready(client: TestClient) -> None:
    assert client.get("/v1/health").json()["status"] == "ok"
    assert client.get("/v1/ready").json() == {"status": "ready"}


def test_login_without_purchase_is_denied_and_leaves_no_user(client: TestClient, env: Env) -> None:
    email = unique_email("semcompra")
    res = login(client, email)
    assert res.status_code == 403
    assert env.sql("SELECT 1 FROM users WHERE email = %s", (email,)) == []
    assert client.get("/v1/me").status_code == 401


def test_login_with_purchase_invite_creates_session(client: TestClient, env: Env) -> None:
    email = unique_email("dono")
    tenant_id = env.tenant("Loja do Dono", email)
    res = login(client, email)
    assert res.status_code == 200
    set_cookie = res.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie
    me = client.get("/v1/me").json()
    assert me["tenant"] == {"id": tenant_id, "role": "owner"}
    assert me["plan"] == {"key": "fase-1", "status": "active"}
    assert "channel.whatsapp" in me["features"] and "ads.google" not in me["features"]
    # convite usado não vale de novo para outro cliente, mas o próprio usuário volta a entrar
    client.cookies.clear()
    assert login(client, email).status_code == 200


def test_unverified_google_email_is_rejected(client: TestClient, env: Env) -> None:
    email = unique_email("naoverif")
    env.tenant("Loja X", email)
    assert login(client, email, verified=False).status_code == 401


def test_expired_invite_is_rejected(client: TestClient, env: Env) -> None:
    email = unique_email("expirado")
    tenant_id = env.tenant("Loja Y", email)
    env.sql(
        "UPDATE pending_invites SET expires_at = now() - interval '1 day' WHERE tenant_id = %s",
        (tenant_id,),
    )
    assert login(client, email).status_code == 403


def test_logout_revokes_session(client: TestClient, env: Env) -> None:
    email = unique_email("sair")
    env.tenant("Loja Z", email)
    login(client, email)
    stolen = client.cookies.get("fm_session")
    assert client.post("/v1/auth/logout").status_code == 200
    client.cookies.set("fm_session", stolen or "")
    assert client.get("/v1/me").status_code == 401


def test_cross_site_post_is_blocked(client: TestClient) -> None:
    res = client.post(
        "/v1/auth/google",
        json={"id_token": sim_token("s", "x@example.test")},
        headers={"Origin": "https://evil.example"},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "bad_origin"


def test_user_cannot_switch_to_foreign_tenant(client: TestClient, env: Env) -> None:
    mine = unique_email("meu")
    env.tenant("Meu cliente", mine)
    foreign = env.tenant("Outro cliente", unique_email("outro"))
    login(client, mine)
    assert client.get("/v1/me", headers={"X-Tenant-Id": foreign}).status_code == 403
    assert client.get("/v1/me", headers={"X-Tenant-Id": "nao-e-uuid"}).status_code == 400
    assert client.get("/v1/me", headers={"X-Tenant-Id": str(uuid.uuid4())}).status_code == 403


def test_canceled_plan_unlocks_nothing(client: TestClient, env: Env) -> None:
    email = unique_email("cancelado")
    tenant_id = env.tenant("Loja Cancelada", email)
    env.sql("UPDATE tenant_plans SET status = 'canceled' WHERE tenant_id = %s", (tenant_id,))
    login(client, email)
    me = client.get("/v1/me").json()
    assert me["features"] == []
    prov = client.get("/v1/providers").json()
    assert all(p["enabled"] is False for p in prov)


def test_providers_catalog_follows_plan(client: TestClient, env: Env) -> None:
    email = unique_email("catalogo")
    env.tenant("Loja Catalogo", email, plan="fase-1")
    login(client, email)
    enabled = {p["key"] for p in client.get("/v1/providers").json() if p["enabled"]}
    assert {"whatsapp_cloud", "messenger", "instagram_dm", "cakto", "hotmart"} <= enabled
    assert not enabled & {"payment_gateway", "meta_ads", "google_ads"}
