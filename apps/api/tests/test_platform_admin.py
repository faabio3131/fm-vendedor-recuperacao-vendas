"""Bloco 20: administração da plataforma, métricas, ID de requisição e teste de carga."""

from __future__ import annotations

import json
import logging
import uuid
from argparse import Namespace
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from fm_seller import cli
from fm_seller import platform_admin as pa
from fm_seller.api.app import create_app
from fm_seller.channels.whatsapp import upsert_conversation
from fm_seller.db import Database
from fm_seller.ops import loadtest
from tests.conftest import ORIGIN, Env, connect_whatsapp, login, unique_email

SECRET_BODY = "texto-secreto-da-conversa-9911"
SECRET_PHONE = "5511933334444"
SECRET_CONTACT_EMAIL = "contato.privado@example.test"


def web(env: Env, db: Database) -> TestClient:
    return TestClient(create_app(env.settings(), db=db, box=env.box), headers={"Origin": ORIGIN})


def plan_status(env: Env, tenant_id: str) -> str:
    return str(env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (tenant_id,))[0][0])


def admin_client(env: Env, db: Database, email: str | None = None) -> tuple[TestClient, str]:
    """Pessoa sem nenhum cliente que virou administradora pelo convite da linha de comando."""
    address = email or unique_email("adm")
    assert pa.invite_admin(env.admin_url, address) == "convite"
    app = create_app(env.settings(), db=db, box=env.box)
    c = TestClient(app, headers={"Origin": ORIGIN})
    c.__enter__()
    assert login(c, address).status_code == 200
    return c, address


@pytest.fixture
def admin(env: Env, db: Database) -> Any:
    c, address = admin_client(env, db)
    yield c, address
    c.__exit__(None, None, None)


def _tenant_with_secrets(client: TestClient, env: Env, db: Database, name: str) -> str:
    owner = unique_email("dono")
    tid = env.tenant(name, owner)
    assert login(client, owner).status_code == 200
    connect_whatsapp(client, env, tid)
    tenant = uuid.UUID(tid)
    with db.tx(tenant_id=tenant) as conn:
        contact, conv = upsert_conversation(conn, tenant, SECRET_PHONE, "Maria Secreta")
    env.sql("UPDATE contacts SET email = %s WHERE id = %s", (SECRET_CONTACT_EMAIL, str(contact)))
    env.sql(
        "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status) "
        "VALUES (%s, %s, 'out', 'bot', %s, 'failed')",
        (tid, str(conv), SECRET_BODY),
    )
    client.post("/v1/auth/logout")
    return tid


# ---------------------------------------------------------------- acesso


def test_only_invited_people_become_admins_and_the_invite_is_used_once(
    env: Env, db: Database
) -> None:
    app = create_app(env.settings(), db=db, box=env.box)
    with TestClient(app, headers={"Origin": ORIGIN}) as stranger:
        assert stranger.get("/v1/admin/me").status_code == 401  # sem login
        nobody = unique_email("ninguem")
        assert login(stranger, nobody).status_code == 403  # sem convite nem cliente: nem entra
    address = unique_email("novo-admin")
    assert pa.invite_admin(env.admin_url, address) == "convite"
    with TestClient(app, headers={"Origin": ORIGIN}) as c:
        assert login(c, address).status_code == 200
        assert c.get("/v1/admin/me").json() == {"email": address}
        assert (
            c.get("/v1/me").status_code == 403
        )  # não é de nenhum cliente: o painel dos clientes não abre
    rows = env.sql(
        "SELECT accepted_at IS NOT NULL FROM platform_admin_invites WHERE email = %s", (address,)
    )
    assert rows == [(True,)]
    with TestClient(app, headers={"Origin": ORIGIN}) as again:
        assert login(again, address).status_code == 200  # volta a entrar sem novo convite
        assert again.get("/v1/admin/me").status_code == 200


def test_expired_invite_does_not_work(env: Env, db: Database) -> None:
    address = unique_email("vencido")
    pa.invite_admin(env.admin_url, address)
    env.sql(
        "UPDATE platform_admin_invites SET expires_at = now() - interval '1 day' WHERE email = %s",
        (address,),
    )
    with TestClient(
        create_app(env.settings(), db=db, box=env.box), headers={"Origin": ORIGIN}
    ) as c:
        assert login(c, address).status_code == 403


def test_existing_person_becomes_admin_at_once_and_can_be_revoked(env: Env, db: Database) -> None:
    owner = unique_email("dono-admin")
    env.tenant("Loja Da Dona Admin", owner)
    with TestClient(
        create_app(env.settings(), db=db, box=env.box), headers={"Origin": ORIGIN}
    ) as c:
        assert login(c, owner).status_code == 200
        assert c.get("/v1/admin/me").status_code == 403  # dona de cliente não é da plataforma
        assert pa.invite_admin(env.admin_url, owner) == "ja_existia"
        assert c.get("/v1/admin/me").status_code == 200
        assert c.get("/v1/me").status_code == 200  # e continua dona do cliente dela
        assert pa.revoke_admin(env.admin_url, owner) is True
        assert c.get("/v1/admin/me").status_code == 403
        assert pa.revoke_admin(env.admin_url, owner) is False


def test_a_client_owner_can_neither_reach_admin_routes_nor_make_themselves_admin(
    client: TestClient, env: Env, db: Database
) -> None:
    owner = unique_email("dono-comum")
    tid = env.tenant("Loja Comum", owner)
    assert login(client, owner).status_code == 200
    for method, path in (
        ("get", "/v1/admin/clients"),
        ("get", "/v1/admin/metrics"),
        ("get", "/v1/admin/health"),
        ("post", f"/v1/admin/clients/{tid}/suspend"),
        ("put", f"/v1/admin/clients/{tid}/plan"),
    ):
        res = getattr(client, method)(path)
        assert res.status_code == 403, path
    assert plan_status(env, tid) == "active"
    user_id = env.sql("SELECT id FROM users WHERE lower(email) = lower(%s)", (owner,))[0][0]
    with (
        pytest.raises(psycopg.Error),
        db.tx(user_id=user_id) as conn,
    ):  # sem convite, o banco recusa
        conn.execute("INSERT INTO platform_admins (user_id) VALUES (%s)", (user_id,))
    with db.tx(user_id=user_id) as conn:
        assert conn.execute("SELECT count(*) AS c FROM platform_admins").fetchone()["c"] == 0  # type: ignore[index]


# ---------------------------------------------------------------- o que a administração enxerga


def test_admin_screens_never_carry_conversation_text_contact_data_or_credentials(
    admin: Any, env: Env, db: Database
) -> None:
    c, _ = admin
    owner_client = TestClient(
        create_app(env.settings(), db=db, box=env.box), headers={"Origin": ORIGIN}
    )
    with owner_client:
        tid = _tenant_with_secrets(owner_client, env, db, "Loja Com Segredos Admin")
    clients = c.get("/v1/admin/clients?limit=200")
    metrics = c.get("/v1/admin/metrics")
    health = c.get("/v1/admin/health")
    assert clients.status_code == metrics.status_code == health.status_code == 200
    blob = clients.text + metrics.text + health.text
    for secret in (SECRET_BODY, SECRET_PHONE, SECRET_CONTACT_EMAIL, "Maria Secreta", "EAAG"):
        assert secret not in blob
    mine = next(i for i in clients.json()["items"] if i["id"] == tid)
    assert mine["name"] == "Loja Com Segredos Admin" and mine["plan"] == "fase-1"
    assert (
        mine["owner"].endswith("@example.test") and "•••" in mine["owner"]
    )  # e-mail do dono mascarado
    assert mine["sends_failed_24h"] == 1 and mine["plan_status"] == "active"
    assert set(health.json()) == {"ok", "exit_code", "findings"}


def test_client_list_is_paginated_and_limited(admin: Any, env: Env) -> None:
    c, _ = admin
    for i in range(3):
        env.tenant(f"Loja Pagina {i}", unique_email("p"))
    first = c.get("/v1/admin/clients?limit=2").json()
    assert len(first["items"]) == 2 and first["total"] >= 3
    second = c.get("/v1/admin/clients?limit=2&offset=2").json()
    assert {i["id"] for i in first["items"]}.isdisjoint({i["id"] for i in second["items"]})
    assert c.get("/v1/admin/clients?limit=0").status_code == 422
    assert c.get("/v1/admin/clients?limit=1000").status_code == 422


def test_metrics_count_without_exposing_content(admin: Any, env: Env, db: Database) -> None:
    c, _ = admin
    before = c.get("/v1/admin/metrics").json()
    owner_client = TestClient(
        create_app(env.settings(), db=db, box=env.box), headers={"Origin": ORIGIN}
    )
    with owner_client:
        _tenant_with_secrets(owner_client, env, db, "Loja Para Metricas")
    after = c.get("/v1/admin/metrics").json()
    assert after["outbox"]["failed_24h"] == before["outbox"]["failed_24h"] + 1
    assert after["clients"]["total"] == before["clients"]["total"] + 1
    assert set(after) == {"generated_at", "worker", "outbox", "recovery", "events", "ai", "clients"}
    assert isinstance(after["clients"]["by_plan_status"], dict)


# ---------------------------------------------------------------- ações auditadas


def _audit_actions(env: Env, tid: str) -> list[str]:
    rows = env.sql(
        "SELECT action FROM audit_log WHERE tenant_id = %s "
        "AND action LIKE 'platform.%%' ORDER BY id",
        (tid,),
    )
    return [r[0] for r in rows]


def test_suspend_and_reactivate_keep_the_data_audit_who_did_it_and_touch_only_that_client(
    admin: Any, env: Env
) -> None:
    c, address = admin
    target = env.tenant("Loja Alvo Da Acao", unique_email("t1"))
    other = env.tenant("Loja Que Nao Muda", unique_email("t2"))
    assert c.post(f"/v1/admin/clients/{target}/suspend").json() == {"status": "suspended"}
    assert c.post(f"/v1/admin/clients/{target}/suspend").status_code == 200  # repetir não dá erro
    assert plan_status(env, target) == "suspended"
    assert env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (other,)) == [
        ("active",)
    ]
    assert env.sql("SELECT status FROM tenants WHERE id = %s", (target,)) == [
        ("active",)
    ]  # login segue
    assert c.post(f"/v1/admin/clients/{target}/reactivate").json() == {"status": "active"}
    assert env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (target,)) == [
        ("active",)
    ]
    assert _audit_actions(env, target) == [
        "platform.suspend",
        "platform.suspend",
        "platform.reactivate",
    ]
    actor = env.sql(
        "SELECT u.email FROM audit_log a JOIN users u ON u.id = a.actor_user_id "
        "WHERE a.tenant_id = %s AND a.action = 'platform.suspend' LIMIT 1",
        (target,),
    )
    assert actor == [(address,)]
    assert c.post(f"/v1/admin/clients/{uuid.uuid4()}/suspend").status_code == 404
    assert c.post("/v1/admin/clients/nao-e-uuid/suspend").status_code == 422


def test_change_plan_and_resend_invite(admin: Any, env: Env) -> None:
    c, _ = admin
    tid = env.tenant("Loja Troca Plano", unique_email("tp"))
    ok = c.put(f"/v1/admin/clients/{tid}/plan", json={"plan": "fase-2"})
    assert ok.status_code == 200 and ok.json() == {"plan": "fase-2"}
    assert env.sql("SELECT plan_key FROM tenant_plans WHERE tenant_id = %s", (tid,)) == [
        ("fase-2",)
    ]
    bad = c.put(f"/v1/admin/clients/{tid}/plan", json={"plan": "plano-que-nao-existe"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "unknown_plan"
    assert (
        c.put(f"/v1/admin/clients/{uuid.uuid4()}/plan", json={"plan": "fase-1"}).status_code == 404
    )
    env.sql(
        "UPDATE pending_invites SET expires_at = now() - interval '2 days' WHERE tenant_id = %s",
        (tid,),
    )
    renewed = c.post(f"/v1/admin/clients/{tid}/resend-invite").json()
    assert renewed["renewed"] == 1 and renewed["days"] == pa.INVITE_DAYS
    valid = env.sql("SELECT expires_at > now() FROM pending_invites WHERE tenant_id = %s", (tid,))
    assert valid == [(True,)]
    env.sql("UPDATE pending_invites SET accepted_at = now() WHERE tenant_id = %s", (tid,))
    none = c.post(f"/v1/admin/clients/{tid}/resend-invite")
    assert none.status_code == 400 and none.json()["error"]["code"] == "no_invite"
    assert _audit_actions(env, tid) == ["platform.change_plan", "platform.resend_invite"]


def test_admin_writes_still_need_the_panel_origin(admin: Any, env: Env) -> None:
    c, _ = admin
    tid = env.tenant("Loja Origem Falsa", unique_email("of"))
    res = c.post(
        f"/v1/admin/clients/{tid}/suspend", headers={"Origin": "https://pagina-maliciosa.example"}
    )
    assert res.status_code == 403
    assert plan_status(env, tid) == "active"


# ---------------------------------------------------------------- ID de requisição e log


def test_request_id_is_on_every_answer_and_hostile_values_are_replaced(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    good = client.get("/v1/health", headers={"x-request-id": "abc-123_ok.456"})
    assert good.headers["x-request-id"] == "abc-123_ok.456"
    for hostile in ("x" * 200, "curto", "tem espaco e <script>", 'a"b\\c' * 4):
        res = client.get("/v1/health", headers={"x-request-id": hostile})
        assert res.headers["x-request-id"] != hostile and len(res.headers["x-request-id"]) == 32
    denied = client.get("/v1/me")
    forged = client.post("/v1/auth/logout", headers={"Origin": "https://outra.example"})
    for res in (denied, forged):
        assert res.headers["x-request-id"]
    assert any(getattr(r, "ctx", {}).get("request_id") == "abc-123_ok.456" for r in caplog.records)


def test_refused_big_body_and_throttled_answers_carry_a_request_id(env: Env, db: Database) -> None:
    settings = env.settings().model_copy(
        update={"max_body_bytes": 1024, "rate_limit_enabled": True, "rate_api_per_min": 2}
    )
    with TestClient(create_app(settings, db=db, box=env.box), headers={"Origin": ORIGIN}) as c:
        big = c.post("/v1/auth/google", content=b"x" * 4000)
        assert big.status_code == 413 and len(big.headers["x-request-id"]) == 32
        c.get("/v1/me")
        c.get("/v1/me")
        limited = c.get("/v1/me")
        assert limited.status_code == 429 and limited.headers["x-request-id"]


# ---------------------------------------------------------------- linha de comando


def test_cli_creates_and_revokes_platform_admins_and_prints_metrics(
    env: Env, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = env.settings()
    address = unique_email("cli-admin")
    assert (
        cli.platform_admin_command(
            settings, Namespace(cmd="create-platform-admin", email=address, days=3)
        )
        == 0
    )
    assert "Convite criado" in capsys.readouterr().out
    assert (
        cli.platform_admin_command(
            settings, Namespace(cmd="create-platform-admin", email="sem-arroba", days=3)
        )
        == 1
    )
    assert (
        cli.platform_admin_command(settings, Namespace(cmd="revoke-platform-admin", email=address))
        == 0
    )
    assert cli.metrics_command(settings) == 0
    out = capsys.readouterr().out
    data = json.loads(out[out.index('{\n  "generated_at"') :])  # o log também sai em JSON
    assert "outbox" in data and "worker" in data


# ---------------------------------------------------------------- teste de carga


def test_loadtest_counts_latency_and_errors_and_refuses_remote_servers_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.path)
        return httpx.Response(500 if request.url.path == "/v1/ready" else 200, json={})

    report = loadtest.run(
        "http://localhost:8000",
        seconds=0.3,
        concurrency=3,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert report.total == len(hits) > 0 and report.rps > 0
    assert report.statuses["200"] > 0 and report.statuses["500"] > 0 and report.errors > 0
    assert report.percentile(50) <= report.percentile(95) <= report.percentile(99)
    assert any("não são a capacidade de produção" in line for line in report.lines())
    assert loadtest.is_local("http://localhost:8000") and not loadtest.is_local(
        "https://api.example.com"
    )
    refused = cli.loadtest_command(
        Namespace(api="https://api.example.com", seconds=1, concurrency=1, remote=False)
    )
    assert refused == 2 and "só roda contra endereço local" in capsys.readouterr().out
