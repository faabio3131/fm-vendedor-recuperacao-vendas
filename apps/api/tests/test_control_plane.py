"""Conexão com o FM Command: desligada por padrão, token de serviço, só agregados, só leitura."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from fm_seller import control_plane as cp
from fm_seller.api.app import create_app
from fm_seller.channels.whatsapp import upsert_conversation
from fm_seller.config import Settings
from fm_seller.db import Database
from tests.conftest import ORIGIN, Env, connect_whatsapp, login, unique_email

TOKEN = "t" * 16 + "token-de-servico-do-fm-command-0123456789"
SNAPSHOT = "/v1/control-plane/fmcc/snapshot"
HEALTH = "/v1/control-plane/fmcc/health"
SECRET_PHONE = "5511955556666"
SECRET_BODY = "texto-privado-da-conversa-4471"
SECRET_CONTACT_EMAIL = "contato.reservado@example.test"
OWNER_NAME_MARK = "Loja Reservada Do Cliente"


def app_client(env: Env, db: Database, **over: Any) -> TestClient:
    cfg = env.settings().model_copy(update={"fmcc_control_plane_token": TOKEN, **over})
    return TestClient(create_app(cfg, db=db, box=env.box), headers={"Origin": ORIGIN})


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def tenant_with_status(env: Env, status: str, name: str | None = None) -> tuple[str, str]:
    owner = unique_email("fmc")
    tid = env.tenant(name or f"Loja {uuid.uuid4().hex[:6]}", owner)
    if status != "active":
        env.sql(
            "UPDATE tenant_plans SET status = %s, status_since = now() WHERE tenant_id = %s",
            (status, tid),
        )
    return tid, owner


# ------------------------------------------------------------------ desligada e autenticação


def test_disabled_by_default_even_with_a_token(env: Env, db: Database) -> None:
    with TestClient(
        create_app(env.settings(), db=db, box=env.box), headers={"Origin": ORIGIN}
    ) as c:
        assert c.get(SNAPSHOT, headers=auth()).status_code == 404
        assert c.get(HEALTH, headers=auth()).status_code == 404


def test_missing_wrong_or_person_session_credentials_are_refused(env: Env, db: Database) -> None:
    tid, owner = tenant_with_status(env, "active")
    with app_client(env, db) as c:
        assert c.get(SNAPSHOT).status_code == 401
        assert c.get(SNAPSHOT, headers={"Authorization": "Basic " + TOKEN}).status_code == 401
        assert c.get(SNAPSHOT, headers=auth("x" * 40)).status_code == 401
        assert c.get(SNAPSHOT, headers=auth(TOKEN[:-1])).status_code == 401
        assert c.get(SNAPSHOT, headers=auth(TOKEN + "x")).status_code == 401
        # Uma pessoa logada (dono de cliente) não ganha acesso por ter sessão.
        assert login(c, owner).status_code == 200
        assert c.get(SNAPSHOT).status_code == 401
        assert tid  # o cliente existe; mesmo assim nada vaza sem o token


def test_token_is_never_echoed_and_must_be_long_enough() -> None:
    with pytest.raises(ValidationError):
        Settings(env="test", fmcc_control_plane_token="curto")
    assert Settings(env="test", fmcc_control_plane_token="").fmcc_control_plane_token == ""
    assert not cp.enabled(Settings(env="test"))
    assert cp.enabled(Settings(env="test", fmcc_control_plane_token="a" * 32))


def test_responses_never_contain_the_token(env: Env, db: Database) -> None:
    with app_client(env, db) as c:
        for path in (SNAPSHOT, HEALTH):
            ok = c.get(path, headers=auth())
            bad = c.get(path, headers=auth("y" * 40))
            assert TOKEN not in ok.text and TOKEN not in bad.text
            assert "y" * 40 not in bad.text


def test_repeated_wrong_tokens_are_throttled(env: Env, db: Database) -> None:
    with app_client(env, db, rate_limit_enabled=True, rate_webhook_fail_per_10min=3) as c:
        codes = [c.get(SNAPSHOT, headers=auth("z" * 40)).status_code for _ in range(5)]
        assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
        assert c.get(SNAPSHOT, headers=auth()).status_code == 429  # a trava vale para o IP


# ------------------------------------------------------------------ conteúdo do snapshot


def test_snapshot_counts_subscriptions_and_emits_only_opaque_facts(env: Env, db: Database) -> None:
    ids = {s: tenant_with_status(env, s)[0] for s in ("active", "canceled", "past_due", "refunded")}
    with app_client(env, db) as c:
        r = c.get(SNAPSHOT, headers=auth())
        assert r.status_code == 200
        body = r.json()
        assert body["schema_version"] == cp.SCHEMA_VERSION
        assert body["product_code"] == "ATENDEVENDEIA"
        assert body["summary"]["customers"] >= 4
        for key in ("active", "past_due", "canceled", "refunded"):
            assert body["summary"][f"{key}_subscriptions"] >= 1
        by_id = {f["external_id"]: f for f in body["facts"]}
        assert f"sub:{ids['active']}:active" in by_id
        assert by_id[f"sub:{ids['active']}:active"]["fact_type"] == "subscription.active"
        assert f"sub:{ids['canceled']}:cancelled" in by_id
        assert by_id[f"sub:{ids['canceled']}:cancelled"]["fact_type"] == "subscription.cancelled"
        # Atraso e reembolso são estados reais, mas não viram fato de ativo nem de cancelado.
        assert not any(ids["past_due"] in k or ids["refunded"] in k for k in by_id)
        for f in body["facts"]:
            assert set(f) == {"external_id", "fact_type", "payload", "source_timestamp"}
            assert f["fact_type"] in {"subscription.active", "subscription.cancelled"}
            assert set(f["payload"]) == {"tenant_id", "plan_key", "status", "product_code"}


def test_snapshot_never_exposes_personal_or_conversation_data(env: Env, db: Database) -> None:
    owner = unique_email("dono-reservado")
    tid = env.tenant(OWNER_NAME_MARK, owner)
    with app_client(env, db) as c:
        assert login(c, owner).status_code == 200
        connect_whatsapp(c, env, tid)
        tenant = uuid.UUID(tid)
        with db.tx(tenant_id=tenant) as conn:
            contact, conv = upsert_conversation(conn, tenant, SECRET_PHONE, "Maria Reservada")
        env.sql(
            "UPDATE contacts SET email = %s WHERE id = %s", (SECRET_CONTACT_EMAIL, str(contact))
        )
        env.sql(
            "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status) "
            "VALUES (%s, %s, 'out', 'bot', %s, 'failed')",
            (tid, str(conv), SECRET_BODY),
        )
        c.post("/v1/auth/logout")
        snap = c.get(SNAPSHOT, headers=auth())
        health = c.get(HEALTH, headers=auth())
    assert snap.status_code == 200 and "facts" in snap.json()  # a resposta existe e tem conteúdo
    everything = snap.text + health.text
    for secret in (
        owner,
        OWNER_NAME_MARK,
        SECRET_PHONE,
        SECRET_BODY,
        SECRET_CONTACT_EMAIL,
        "Maria Reservada",
        TOKEN,
    ):
        assert secret not in everything
    assert "@" not in snap.text  # nenhum e-mail, de ninguém, em lugar nenhum do snapshot


def test_missing_is_not_zero_coverage_marks_what_is_unavailable(env: Env, db: Database) -> None:
    with app_client(env, db) as c:
        body = c.get(SNAPSHOT, headers=auth()).json()
    for key in ("billing_invoices", "payments", "receivables", "infrastructure_cost", "leads"):
        assert body["coverage"][key].startswith("unavailable")
    flat = json.dumps(body["summary"]) + json.dumps(body["operations"])
    for invented in ("mrr", "arr", "revenue", "invoice", "cash", "cost", "lead", "ticket"):
        assert invented not in flat  # nada de número inventado onde não há fonte
    assert body["coverage"]["subscriptions"].startswith("available")


def test_snapshot_is_read_only_and_fresh_each_time(env: Env, db: Database) -> None:
    before = env.sql("SELECT count(*) FROM audit_log")[0][0]
    with app_client(env, db) as c:
        first = c.get(SNAPSHOT, headers=auth()).json()
        tenant_with_status(env, "active")
        second = c.get(SNAPSHOT, headers=auth()).json()
    assert env.sql("SELECT count(*) FROM audit_log")[0][0] == before + 1  # só o cliente criado
    assert second["summary"]["customers"] == first["summary"]["customers"] + 1


def test_health_reports_codes_only_and_matches_the_http_status(env: Env, db: Database) -> None:
    with app_client(env, db) as c:
        r = c.get(HEALTH, headers=auth())
    body = r.json()
    assert body["product_code"] == "ATENDEVENDEIA"
    assert body["status"] in {"ok", "degraded"}
    assert (r.status_code == 200) == (body["status"] == "ok")
    assert all(set(f) == {"level", "code"} for f in body["findings"])


def test_responses_carry_security_headers_and_no_store(env: Env, db: Database) -> None:
    with app_client(env, db) as c:
        for path in (SNAPSHOT, HEALTH):
            r = c.get(path, headers=auth())
            assert r.headers["cache-control"] == "no-store"
            assert r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["x-request-id"]


def test_correlation_id_from_fm_command_becomes_the_request_id(env: Env, db: Database) -> None:
    mine = "fmcc-" + uuid.uuid4().hex
    with app_client(env, db) as c:
        r = c.get(SNAPSHOT, headers={**auth(), "x-correlation-id": mine})
        assert r.headers["x-request-id"] == mine
        hostile = c.get(SNAPSHOT, headers={**auth(), "x-correlation-id": "x y\tz"})
        assert hostile.headers["x-request-id"] != "x y\tz"
