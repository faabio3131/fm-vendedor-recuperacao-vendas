"""Acesso ao banco. Todo acesso passa por `Database.tx`, que fixa o contexto de isolamento (RLS)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

Conn = psycopg.Connection[DictRow]


class Database:
    def __init__(self, url: str, *, max_size: int = 10) -> None:
        self._pool: ConnectionPool[Conn] = ConnectionPool(
            url,
            min_size=1,
            max_size=max_size,
            open=False,
            kwargs={"row_factory": dict_row},
        )

    def open(self) -> None:
        self._pool.open(wait=True, timeout=15)

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def tx(
        self,
        *,
        tenant_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        google_sub: str | None = None,
        login_email: str | None = None,
        session_hash: str | None = None,
        system: bool = False,
    ) -> Iterator[Conn]:
        """Abre uma transação com o contexto de isolamento. Valores valem só nesta transação."""
        ctx: dict[str, str] = {}
        if tenant_id is not None:
            ctx["app.tenant_id"] = str(tenant_id)
        if user_id is not None:
            ctx["app.user_id"] = str(user_id)
        if google_sub is not None:
            ctx["app.google_sub"] = google_sub
        if login_email is not None:
            ctx["app.login_email"] = login_email.lower()
        if session_hash is not None:
            ctx["app.session_hash"] = session_hash
        if system:
            ctx["app.system"] = "on"
        with self._pool.connection() as conn, conn.transaction():
            for key, value in ctx.items():
                conn.execute("SELECT set_config(%s, %s, true)", (key, value))
            yield conn

    def set_tenant(self, conn: Conn, tenant_id: uuid.UUID) -> None:
        """Troca o cliente de contexto dentro da transação (usado ao aceitar convite)."""
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))

    def set_user(self, conn: Conn, user_id: uuid.UUID) -> None:
        """Define o usuário de contexto na transação (depois de achar a sessão ou o login)."""
        conn.execute("SELECT set_config('app.user_id', %s, true)", (str(user_id),))

    def ping(self) -> bool:
        try:
            with self.tx() as conn:
                conn.execute("SELECT 1")
        except psycopg.Error:
            return False
        return True


def row_get(row: dict[str, Any] | None, key: str) -> Any:
    return None if row is None else row[key]
