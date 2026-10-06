"""Banco que derruba conexões paradas (ex.: Neon grátis, que suspende o banco após 5 min)."""

from __future__ import annotations

import psycopg

from fm_seller.db import Database

from .conftest import Env


def test_conexao_fechada_pelo_servidor_nao_derruba_o_pedido(env: Env) -> None:
    database = Database(env.app_url)
    database.open()
    try:
        with database.tx() as conn:  # aquece o pool com uma conexão de verdade
            row = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()
        assert row is not None
        # O servidor fecha a conexão parada, como o provedor faz ao suspender o banco.
        with psycopg.connect(env.app_url, autocommit=True) as killer:
            killer.execute("SELECT pg_terminate_backend(%s)", (row["pid"],))
        # O primeiro uso depois da queda precisa funcionar (a conexão morta é trocada).
        with database.tx() as conn:
            again = conn.execute("SELECT 1 AS ok").fetchone()
        assert again is not None and again["ok"] == 1
    finally:
        database.close()
