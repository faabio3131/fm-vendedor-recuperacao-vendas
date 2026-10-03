"""Prova no banco real que um cliente não enxerga nem grava dados de outro."""

from __future__ import annotations

import uuid

import psycopg
import pytest

from fm_seller.db import Database
from tests.conftest import Env


def _seed_connection(env: Env, tenant_id: str, provider: str) -> None:
    env.sql(
        "INSERT INTO connections (tenant_id, provider, public_id, config_encrypted) "
        "VALUES (%s, %s, %s, %s)",
        (tenant_id, provider, uuid.uuid4().hex, b"x"),
    )


def test_app_role_cannot_bypass_rls(env: Env, db: Database) -> None:
    with db.tx() as conn:
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
    assert row == {"rolsuper": False, "rolbypassrls": False}


def test_tenant_only_sees_own_connections(env: Env, db: Database) -> None:
    a = env.tenant("Cliente A", "a@example.test")
    b = env.tenant("Cliente B", "b@example.test")
    _seed_connection(env, a, "cakto")
    _seed_connection(env, b, "hotmart")
    with db.tx(tenant_id=uuid.UUID(a)) as conn:
        providers = [r["provider"] for r in conn.execute("SELECT provider FROM connections")]
    assert providers == ["cakto"]


def test_no_context_sees_nothing(env: Env, db: Database) -> None:
    t = env.tenant("Cliente C", "c@example.test")
    _seed_connection(env, t, "cakto")
    with db.tx() as conn:
        assert conn.execute("SELECT count(*) AS n FROM connections").fetchone() == {"n": 0}
        assert conn.execute("SELECT count(*) AS n FROM tenants").fetchone() == {"n": 0}
        assert conn.execute("SELECT count(*) AS n FROM users").fetchone() == {"n": 0}
        assert conn.execute("SELECT count(*) AS n FROM audit_log").fetchone() == {"n": 0}


def test_cannot_write_into_another_tenant(env: Env, db: Database) -> None:
    a = env.tenant("Cliente D", "d@example.test")
    b = env.tenant("Cliente E", "e@example.test")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.tx(tenant_id=uuid.UUID(a)) as conn:
        conn.execute(
            "INSERT INTO connections (tenant_id, provider, public_id, config_encrypted) "
            "VALUES (%s, 'cakto', %s, %s)",
            (b, uuid.uuid4().hex, b"x"),
        )


def test_cannot_update_or_delete_other_tenants_rows(env: Env, db: Database) -> None:
    a = env.tenant("Cliente F", "f@example.test")
    b = env.tenant("Cliente G", "g@example.test")
    _seed_connection(env, b, "cakto")
    with db.tx(tenant_id=uuid.UUID(a)) as conn:
        upd = conn.execute("UPDATE connections SET status = 'connected'")
        dele = conn.execute("DELETE FROM connections")
        assert upd.rowcount == 0
        assert dele.rowcount == 0
    assert env.sql("SELECT status FROM connections WHERE tenant_id = %s", (b,)) == [("pending",)]


def test_app_cannot_create_tenants_or_plans_without_system_mode(env: Env, db: Database) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.tx() as conn:
        conn.execute("INSERT INTO tenants (name) VALUES ('invasor')")
    t = env.tenant("Cliente H", "h@example.test")
    with db.tx(tenant_id=uuid.UUID(t)) as conn:
        result = conn.execute("UPDATE tenant_plans SET plan_key = 'fase-4'")
        assert result.rowcount == 0  # o cliente não troca o próprio plano
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.tx() as conn:
        conn.execute("UPDATE plans SET features = ARRAY['ads.google']")


def test_audit_log_is_append_only(env: Env, db: Database) -> None:
    t = env.tenant("Cliente I", "i@example.test")
    tid = uuid.UUID(t)
    with db.tx(tenant_id=tid) as conn:
        conn.execute("INSERT INTO audit_log (tenant_id, action) VALUES (%s, 'teste')", (tid,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.tx(tenant_id=tid) as conn:
        conn.execute("DELETE FROM audit_log")
    # Nem o dono altera: sem política de UPDATE/DELETE a linha nem é visível para a operação.
    with psycopg.connect(env.admin_url) as owner:
        owner.execute("SELECT set_config('app.system', 'on', true)")
        upd = owner.execute("UPDATE audit_log SET action = 'adulterado' WHERE tenant_id = %s", (t,))
        dele = owner.execute("DELETE FROM audit_log WHERE tenant_id = %s", (t,))
        assert upd.rowcount == 0 and dele.rowcount == 0
    actions = [r[0] for r in env.sql("SELECT action FROM audit_log WHERE tenant_id = %s", (t,))]
    assert "teste" in actions and "adulterado" not in actions


def test_users_are_not_listable_across_tenants(env: Env, db: Database) -> None:
    t1 = env.tenant("Cliente J", "j@example.test")
    t2 = env.tenant("Cliente K", "k@example.test")
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    env.sql("INSERT INTO users (id, google_sub, email) VALUES (%s, 'sj', 'j@example.test')", (u1,))
    env.sql("INSERT INTO users (id, google_sub, email) VALUES (%s, 'sk', 'k@example.test')", (u2,))
    env.sql("INSERT INTO memberships (tenant_id, user_id, role) VALUES (%s, %s, 'owner')", (t1, u1))
    env.sql("INSERT INTO memberships (tenant_id, user_id, role) VALUES (%s, %s, 'owner')", (t2, u2))
    with db.tx(tenant_id=uuid.UUID(t1)) as conn:
        emails = [r["email"] for r in conn.execute("SELECT email FROM users")]
    assert emails == ["j@example.test"]
