"""Runner de migrations SQL versionadas. Aplica em ordem, uma transação por arquivo."""

from __future__ import annotations

import hashlib
from importlib import resources

import psycopg


def _migrations() -> list[tuple[str, str]]:
    root = resources.files("fm_seller") / "migrations"
    files = sorted((p for p in root.iterdir() if p.name.endswith(".sql")), key=lambda p: p.name)
    return [(p.name, p.read_text(encoding="utf-8")) for p in files]


def apply_migrations(admin_url: str) -> list[str]:
    """Aplica as migrations pendentes e devolve os nomes aplicados agora."""
    applied_now: list[str] = []
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version text PRIMARY KEY, checksum text NOT NULL, "
            "applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {
            row[0]: row[1]
            for row in conn.execute("SELECT version, checksum FROM schema_migrations")
        }
        for name, sql in _migrations():
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            if name in done:
                if done[name] != checksum:
                    raise RuntimeError(f"Migration {name} foi alterada depois de aplicada")
                continue
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                    (name, checksum),
                )
            applied_now.append(name)
    return applied_now
