"""Primeira subida sem terminal (Render grátis): papel do app, migrations e primeiro cliente."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from pydantic import SecretStr

from fm_seller.bootstrap import BootstrapError, ensure_app_role, run_bootstrap
from fm_seller.config import Settings
from tests.conftest import OWNER, Env
from tests.test_ops import drop_db


@pytest.fixture
def empty_env() -> Iterator[Env]:
    name = f"fm_boot_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(f"{OWNER}/postgres", autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    yield Env(name)
    drop_db(name)


def _settings(e: Env, **over: object) -> Settings:
    return Settings(**{**e.settings().model_dump(), **over})


def test_existing_app_role_is_kept_and_checked(empty_env: Env) -> None:
    assert ensure_app_role(empty_env.admin_url, "qualquer-senha") == "exists"


def test_cannot_create_role_gives_a_clear_stop_message(empty_env: Env) -> None:
    # Aqui o dono do banco não tem CREATEROLE: mesmo caso de um usuário padrão sem permissão.
    with pytest.raises(BootstrapError, match="não pode criar papéis"):
        ensure_app_role(empty_env.admin_url, "senha", role=f"fm_x_{uuid.uuid4().hex[:6]}")


def test_bootstrap_migrates_and_creates_first_tenant_once(empty_env: Env) -> None:
    cfg = _settings(
        empty_env,
        bootstrap_owner_email="dono@example.test",
        bootstrap_tenant_name="Loja Inicial",
    )
    first = run_bootstrap(cfg)
    assert any(line.startswith("migrations: 0001") for line in first)
    assert first[-1].startswith("primeiro cliente: criado")
    invites = empty_env.sql("SELECT email FROM pending_invites")
    assert [r[0] for r in invites] == ["dono@example.test"]

    again = run_bootstrap(cfg)
    assert "migrations: já em dia" in again
    assert again[-1].startswith("primeiro cliente: já existe")
    assert empty_env.sql("SELECT count(*) FROM tenants")[0][0] == 1


def test_bootstrap_without_owner_email_creates_no_tenant(empty_env: Env) -> None:
    out = run_bootstrap(_settings(empty_env))
    assert not any("primeiro cliente" in line for line in out)
    assert empty_env.sql("SELECT count(*) FROM tenants")[0][0] == 0


def test_password_never_appears_in_output(empty_env: Env) -> None:
    cfg = _settings(empty_env, bootstrap_app_password=SecretStr("senha-super-secreta-xyz"))
    assert "senha-super-secreta-xyz" not in " ".join(run_bootstrap(cfg))
