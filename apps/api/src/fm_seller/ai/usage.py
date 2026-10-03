"""Uso da IA e limite do plano. O limite é dado do plano (`plans.limits`), não está no código."""

from __future__ import annotations

import uuid
from typing import Any

from fm_seller.db import Conn

LIMIT_KEY = "ai_replies_per_month"


def monthly_limit(conn: Conn, tenant_id: uuid.UUID) -> int | None:
    """Respostas de IA por mês no plano do cliente. None = sem limite."""
    row = conn.execute(
        "SELECT p.limits FROM tenant_plans tp JOIN plans p ON p.key = tp.plan_key "
        "WHERE tp.tenant_id = %s",
        (tenant_id,),
    ).fetchone()
    value = (row["limits"] if row else {}).get(LIMIT_KEY)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def month_usage(conn: Conn, tenant_id: uuid.UUID, tz: str) -> dict[str, int]:
    row = conn.execute(
        "SELECT coalesce(sum(calls), 0) AS calls, coalesce(sum(failures), 0) AS failures, "
        "coalesce(sum(tokens_in), 0) AS tokens_in, coalesce(sum(tokens_out), 0) AS tokens_out "
        "FROM ai_usage WHERE tenant_id = %s "
        "AND day >= date_trunc('month', (now() AT TIME ZONE %s))::date",
        (tenant_id, tz),
    ).fetchone()
    assert row is not None
    return {k: int(v) for k, v in row.items()}


def record(
    conn: Conn,
    tenant_id: uuid.UUID,
    tz: str,
    *,
    ok: bool,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    """Soma uma chamada ao modelo no dia (fuso do cliente); só as bem-sucedidas contam no limite."""
    conn.execute(
        "INSERT INTO ai_usage (tenant_id, day, calls, failures, tokens_in, tokens_out) "
        "VALUES (%s, (now() AT TIME ZONE %s)::date, %s, %s, %s, %s) "
        "ON CONFLICT (tenant_id, day) DO UPDATE SET calls = ai_usage.calls + EXCLUDED.calls, "
        "failures = ai_usage.failures + EXCLUDED.failures, "
        "tokens_in = ai_usage.tokens_in + EXCLUDED.tokens_in, "
        "tokens_out = ai_usage.tokens_out + EXCLUDED.tokens_out",
        (tenant_id, tz, 1 if ok else 0, 0 if ok else 1, tokens_in, tokens_out),
    )


def summary(conn: Conn, tenant_id: uuid.UUID, tz: str) -> dict[str, Any]:
    used = month_usage(conn, tenant_id, tz)
    limit = monthly_limit(conn, tenant_id)
    return {
        "replies": used["calls"],
        "failures": used["failures"],
        "tokens_in": used["tokens_in"],
        "tokens_out": used["tokens_out"],
        "limit": limit,
        "percent": None if not limit else min(100, round(used["calls"] * 100 / limit)),
    }
