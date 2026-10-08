"""Bloco 19: segurança da borda. Limites contra abuso, tamanho de corpo, cabeçalhos e sessões."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from fm_seller.api.app import create_app
from fm_seller.api.guards import client_ip, security_headers
from fm_seller.db import Database
from fm_seller.logging_setup import JsonFormatter
from fm_seller.security.ratelimit import MAX_KEYS, RateLimiter
from fm_seller.services import MAX_SESSIONS, NO_ACCESS
from tests.conftest import ORIGIN, Env, login, sim_token, unique_email

SRC = Path(__file__).resolve().parents[1] / "src" / "fm_seller"
HEADERS = (
    "content-security-policy",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
)


def limited_client(env: Env, db: Database, **over: Any) -> Iterator[TestClient]:
    settings = env.settings().model_copy(update={"rate_limit_enabled": True, **over})
    with TestClient(create_app(settings, db=db, box=env.box), headers={"Origin": ORIGIN}) as c:
        yield c


@pytest.fixture
def strict(env: Env, db: Database) -> Iterator[TestClient]:
    """App com limites minúsculos para dar para estourar em poucos pedidos."""
    yield from limited_client(
        env,
        db,
        rate_login_per_5min=3,
        rate_api_per_min=50,
        rate_sensitive_per_10min=2,
        rate_webhook_fail_per_10min=3,
        rate_webhook_per_min=50,
        max_body_bytes=2048,
        trust_proxy=True,
    )


# ---------------------------------------------------------------- limitador


def test_rate_limiter_slides_the_window_and_reports_when_to_retry() -> None:
    now = [100.0]
    lim = RateLimiter(clock=lambda: now[0])
    assert [lim.check("k", 3, 60)[0] for _ in range(3)] == [True, True, True]
    ok, wait = lim.check("k", 3, 60)
    assert ok is False and 1 <= wait <= 61
    assert lim.check("outra", 3, 60)[0] is True  # cada chave tem a própria conta
    now[0] += 59
    assert lim.check("k", 3, 60)[0] is False
    now[0] += 2  # passou da janela
    assert lim.check("k", 3, 60)[0] is True
    assert lim.count("k", 60) == 1


def test_rate_limiter_memory_is_bounded() -> None:
    now = [0.0]
    lim = RateLimiter(clock=lambda: now[0])
    for i in range(MAX_KEYS + 500):
        now[0] += 0.001
        lim.check(f"atacante-{i}", 5, 600)
    assert len(lim._hits) <= MAX_KEYS  # trocar de chave não enche a memória


# ---------------------------------------------------------------- força bruta e enxurrada


def test_login_brute_force_is_throttled_per_ip_and_other_ips_are_not_affected(
    strict: TestClient,
) -> None:
    bad = {"id_token": "x" * 20}
    first = [strict.post("/v1/auth/google", json=bad).status_code for _ in range(3)]
    assert first == [401, 401, 401]
    blocked = strict.post("/v1/auth/google", json=bad)
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1
    other = strict.post("/v1/auth/google", json=bad, headers={"X-Forwarded-For": "203.0.113.9"})
    assert other.status_code == 401  # outro IP segue normal
    for name in HEADERS:
        assert name in blocked.headers  # a própria recusa leva os cabeçalhos de segurança


def test_forged_forwarded_for_is_ignored_unless_the_proxy_is_trusted(
    env: Env, db: Database
) -> None:
    for client in limited_client(env, db, rate_login_per_5min=2, trust_proxy=False):
        bad = {"id_token": "x" * 20}
        for i in range(2):
            res = client.post(
                "/v1/auth/google", json=bad, headers={"X-Forwarded-For": f"9.9.9.{i}"}
            )
            assert res.status_code == 401
        spoof = client.post("/v1/auth/google", json=bad, headers={"X-Forwarded-For": "1.1.1.1"})
        assert spoof.status_code == 429  # trocar o cabeçalho não escapa do limite


def test_client_ip_uses_the_last_forwarded_entry_only_when_trusted() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"6.6.6.6, 10.0.0.7")],
            "client": ("172.16.0.1", 5000),
        }
    )
    assert (
        client_ip(request, trust_proxy=True) == "10.0.0.7"
    )  # a entrada do proxy, não a do cliente
    assert client_ip(request, trust_proxy=False) == "172.16.0.1"


def test_flood_guard_never_blocks_health_checks(strict: TestClient, env: Env, db: Database) -> None:
    for client in limited_client(env, db, rate_api_per_min=3):
        assert [client.get("/v1/me").status_code for _ in range(3)] == [401, 401, 401]
        assert client.get("/v1/me").status_code == 429
        assert all(client.get("/v1/health").status_code == 200 for _ in range(10))
        assert client.get("/v1/ready").status_code == 200


def test_sensitive_actions_are_limited_per_account_not_per_ip(strict: TestClient, env: Env) -> None:
    env.tenant("Loja Sensivel A", email_a := unique_email("sa"))
    env.tenant("Loja Sensivel B", email_b := unique_email("sb"))
    assert login(strict, email_a).status_code == 200
    body = {"identifier": "11900000000"}
    codes = [strict.post("/v1/privacy/contacts/export", json=body).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    strict.post("/v1/auth/logout")
    assert login(strict, email_b).status_code == 200  # mesma máquina, outra conta
    assert strict.post("/v1/privacy/contacts/export", json=body).status_code == 200


def test_repeated_rejected_webhooks_lock_that_ip_but_not_others(strict: TestClient) -> None:
    for _ in range(3):
        res = strict.post("/v1/webhooks/cakto/conexao-inexistente", json={})
        assert res.status_code == 404
    locked = strict.post("/v1/webhooks/cakto/conexao-inexistente", json={})
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) >= 1
    other = strict.post(
        "/v1/webhooks/cakto/conexao-inexistente",
        json={},
        headers={"X-Forwarded-For": "198.51.100.4"},
    )
    assert other.status_code == 404
    platform = strict.post("/v1/platform/webhooks/cakto", json={})
    assert platform.status_code == 429  # mesma trava vale para a compra do SaaS


# ---------------------------------------------------------------- tamanho do corpo


def test_big_body_is_refused_before_reading_it_and_in_chunks(strict: TestClient) -> None:
    declared = strict.post("/v1/webhooks/cakto/x", content=b"x" * 5000)
    assert declared.status_code == 413 and declared.json()["error"]["code"] == "payload_too_large"
    assert "content-security-policy" in declared.headers
    chunks = (b"x" * 1000 for _ in range(6))  # sem Content-Length: vem em pedaços
    streamed = strict.post("/v1/webhooks/cakto/x", content=chunks)
    assert streamed.status_code == 413
    login_body = strict.post("/v1/auth/google", content=b'{"id_token":"' + b"a" * 5000 + b'"}')
    assert login_body.status_code == 413
    small = strict.post("/v1/webhooks/cakto/x", json={})
    assert small.status_code == 404  # corpo pequeno segue o caminho normal


# ---------------------------------------------------------------- cabeçalhos


def test_every_api_answer_carries_security_headers_and_private_data_is_not_cached(
    client: TestClient,
) -> None:
    ok = client.get("/v1/health")
    denied = client.get("/v1/me")
    forged = client.post("/v1/auth/logout", headers={"Origin": "https://outro.example"})
    for res in (ok, denied, forged):
        for name in HEADERS:
            assert name in res.headers, (res.status_code, name)
        assert res.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
        assert "strict-transport-security" not in res.headers  # fora de staging/produção
    assert denied.headers["cache-control"] == "no-store"
    assert forged.status_code == 403


def test_hsts_and_hidden_api_description_only_in_staging_and_production(
    env: Env, db: Database
) -> None:
    dev = TestClient(create_app(env.settings(), db=db, box=env.box))
    assert dev.get("/openapi.json").status_code == 200  # em desenvolvimento ajuda quem programa
    staging = env.settings().model_copy(
        update={"env": "staging", "google_client_id": "1-a.apps.googleusercontent.com"}
    )
    assert "Strict-Transport-Security" in security_headers(staging)
    hidden = TestClient(create_app(staging, db=db, box=env.box))
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert hidden.get(path).status_code == 404
    assert "strict-transport-security" in hidden.get("/v1/health").headers


# ---------------------------------------------------------------- sessões


def test_logout_all_ends_every_session_of_the_person(env: Env, db: Database) -> None:
    email = unique_email("multi")
    env.tenant("Loja Varios Aparelhos", email)
    app = create_app(env.settings(), db=db, box=env.box)
    with (
        TestClient(app, headers={"Origin": ORIGIN}) as phone,
        TestClient(app, headers={"Origin": ORIGIN}) as laptop,
    ):
        assert login(phone, email).status_code == 200 and login(laptop, email).status_code == 200
        assert phone.get("/v1/me").status_code == 200 and laptop.get("/v1/me").status_code == 200
        res = phone.post("/v1/auth/logout-all")
        assert res.status_code == 200 and res.json()["closed"] >= 2
        assert phone.get("/v1/me").status_code == 401
        assert laptop.get("/v1/me").status_code == 401  # o outro aparelho também caiu
        assert login(laptop, email).status_code == 200  # e dá para entrar de novo
    assert (
        TestClient(app, headers={"Origin": ORIGIN}).post("/v1/auth/logout-all").status_code == 401
    )


def test_only_the_most_recent_sessions_stay_active(env: Env, db: Database) -> None:
    email = unique_email("cap")
    env.tenant("Loja Muitas Sessoes", email)
    app = create_app(env.settings(), db=db, box=env.box)
    with TestClient(app, headers={"Origin": ORIGIN}) as c:
        assert login(c, email).status_code == 200
        oldest = c.cookies.get("fm_session")
        for _ in range(MAX_SESSIONS + 1):
            assert login(c, email).status_code == 200
        user_id = env.sql("SELECT id FROM users WHERE lower(email) = lower(%s)", (email,))[0][0]
        with db.tx(user_id=user_id) as conn:
            row = conn.execute(
                "SELECT count(*) AS c FROM sessions WHERE revoked_at IS NULL"
            ).fetchone()
        assert row is not None and row["c"] == MAX_SESSIONS
        assert c.get("/v1/me").status_code == 200  # a atual segue valendo
        c.cookies.clear()
        c.cookies.set("fm_session", oldest or "")
        assert c.get("/v1/me").status_code == 401  # a mais antiga foi encerrada


def test_login_does_not_reveal_which_accounts_exist(env: Env, db: Database) -> None:
    app = create_app(env.settings(), db=db, box=env.box)
    with TestClient(app, headers={"Origin": ORIGIN}) as c:
        stranger = c.post(
            "/v1/auth/google", json={"id_token": sim_token("sub-estranho", "estranho@example.test")}
        )
        email = unique_email("antigo")
        env.tenant("Loja Que Perdeu O Acesso", email)
        assert login(c, email).status_code == 200
        env.sql(
            "DELETE FROM memberships WHERE user_id IN (SELECT id FROM users WHERE email = %s)",
            (email,),
        )
        c.post("/v1/auth/logout")
        known = login(c, email)
    assert stranger.status_code == known.status_code == 403
    assert stranger.json() == known.json() and stranger.json()["error"]["message"] == NO_ACCESS


def test_session_cookie_is_http_only_same_site_and_secure_when_configured(
    env: Env, db: Database
) -> None:
    email = unique_email("cookie")
    env.tenant("Loja Cookie", email)
    secure = env.settings().model_copy(update={"cookie_secure": True})
    with TestClient(create_app(secure, db=db, box=env.box), headers={"Origin": ORIGIN}) as c:
        res = login(c, email)
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert "max-age" in cookie


# ---------------------------------------------------------------- injeção, SSRF e log


def test_only_known_modules_call_out_to_the_network() -> None:
    """Nenhuma URL vinda do cliente é buscada: só estes módulos falam com a rede, e cada um com
    endereço fixo ou configurado pela plataforma. Módulo novo que faça isso precisa de revisão."""
    pattern = re.compile(
        r"^\s*(?:import|from)\s+(httpx|requests|aiohttp|urllib\.request|http\.client|socket)\b"
        r"|PyJWKClient",
        re.MULTILINE,
    )
    found = {
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if pattern.search(p.read_text(encoding="utf-8"))
    }
    assert found == {
        "ai/gemini.py",  # base FM_AI_BASE_URL, da plataforma
        "auth/google.py",  # certificados do Google, endereço fixo
        "channels/meta_api.py",  # FM_META_GRAPH_BASE, da plataforma
        # API de licenças do Command: FM_FMCOMMAND_API_BASE_URL, da plataforma; só https, sem
        # redirecionamento, só GET e desligada por padrão (ADR-0005).
        "licensing/reconcile.py",
        "ops/loadtest.py",  # linha de comando; só endereço local, a menos que o operador confirme
        "ops/smoke.py",  # só roda pela linha de comando, contra o endereço que o operador passou
    }


def test_user_controlled_text_cannot_forge_a_log_line() -> None:
    forged = 'ok\n{"level": "CRITICAL", "msg": "linha forjada"}'
    record = logging.LogRecord("x", logging.INFO, __file__, 1, forged, None, None)
    line = JsonFormatter().format(record)
    assert "\n" not in line and json.loads(line)["msg"] == forged  # uma linha só, texto como dado
