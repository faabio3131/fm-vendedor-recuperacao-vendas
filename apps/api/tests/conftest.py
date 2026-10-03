from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from fm_seller.api.app import create_app
from fm_seller.cli import create_tenant
from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.migrate import apply_migrations
from fm_seller.security.crypto import SecretBox

PG_HOST = os.environ.get("FM_TEST_PG_HOST", "localhost")
PG_PORT = os.environ.get("FM_TEST_PG_PORT", "5432")
OWNER = f"postgresql://fm_owner:dev_owner_only@{PG_HOST}:{PG_PORT}"
APP = f"postgresql://fm_app:dev_app_only@{PG_HOST}:{PG_PORT}"
ORIGIN = "http://localhost:3000"


class Env:
    def __init__(self, dbname: str) -> None:
        self.admin_url = f"{OWNER}/{dbname}"
        self.app_url = f"{APP}/{dbname}"
        self.key_spec = SecretBox.generate_key_spec()
        self.box = SecretBox(self.key_spec)

    def settings(self) -> Settings:
        return Settings(
            env="test",
            database_url=self.app_url,
            database_admin_url=self.admin_url,
            secrets_keys=self.key_spec,
            web_origin=ORIGIN,
            public_base_url="https://api.example.test",
        )

    def tenant(self, name: str, email: str, plan: str = "fase-1", role: str = "owner") -> str:
        return create_tenant(self.admin_url, name, email, plan, role)

    def invite(self, tenant_id: str, email: str, role: str) -> None:
        with psycopg.connect(self.admin_url) as conn:
            conn.execute("SELECT set_config('app.system', 'on', true)")
            conn.execute(
                "INSERT INTO pending_invites (tenant_id, email, role, expires_at) "
                "VALUES (%s, lower(%s), %s, now() + interval '7 days')",
                (tenant_id, email, role),
            )
            conn.commit()

    def sql(self, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with psycopg.connect(self.admin_url) as conn:
            conn.execute("SELECT set_config('app.system', 'on', true)")
            cur = conn.execute(query, params)
            rows = cur.fetchall() if cur.description else []
            conn.commit()
            return rows


@pytest.fixture(scope="session")
def env() -> Iterator[Env]:
    dbname = f"fm_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(f"{OWNER}/postgres", autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    e = Env(dbname)
    apply_migrations(e.admin_url)
    yield e
    with psycopg.connect(f"{OWNER}/postgres", autocommit=True) as conn:
        conn.execute(f'DROP DATABASE "{dbname}" WITH (FORCE)')


@pytest.fixture(scope="session")
def db(env: Env) -> Iterator[Database]:
    database = Database(env.app_url)
    database.open()
    yield database
    database.close()


@pytest.fixture
def client(env: Env, db: Database) -> Iterator[TestClient]:
    app = create_app(env.settings(), db=db, box=env.box)
    with TestClient(app, headers={"Origin": ORIGIN}) as c:
        yield c


def sim_token(sub: str, email: str, name: str = "Pessoa Teste", verified: bool = True) -> str:
    return f"sim|{sub}|{email}|{name}|{'1' if verified else '0'}"


def login(client: TestClient, email: str, verified: bool = True) -> Any:
    return client.post(
        "/v1/auth/google",
        json={"id_token": sim_token("sub-" + email, email, verified=verified)},
    )


def unique_email(prefix: str = "u") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.test"
