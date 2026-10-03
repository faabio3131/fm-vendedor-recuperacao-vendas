"""Operações sobre casos de recuperação usadas por mais de um módulo (sem dependência de canal)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fm_seller.db import Conn


def close_case(
    conn: Conn,
    case_id: uuid.UUID,
    status: str,
    reason: str,
    now: datetime,
    recovered_amount: int | None = None,
) -> None:
    """Encerra o caso e cancela o que ainda estava agendado."""
    conn.execute(
        "UPDATE recovery_cases SET status = %s, closed_reason = %s, "
        "closed_at = COALESCE(closed_at, %s), recovered_amount_cents = %s WHERE id = %s",
        (status, reason, now, recovered_amount, case_id),
    )
    conn.execute(
        "UPDATE recovery_steps SET status = 'canceled', detail = %s "
        "WHERE case_id = %s AND status = 'scheduled'",
        (reason, case_id),
    )


def stop_cold_cases(conn: Conn, tenant_id: uuid.UUID, contact_id: uuid.UUID, now: datetime) -> int:
    """O cliente voltou a escrever: a conversa está viva, então para a recuperação dela."""
    rows = conn.execute(
        "SELECT id FROM recovery_cases WHERE tenant_id = %s AND contact_id = %s "
        "AND source = 'conversa' AND status = 'open'",
        (tenant_id, contact_id),
    ).fetchall()
    for row in rows:
        close_case(conn, row["id"], "stopped", "cliente_respondeu", now)
    return len(rows)
