"""Governed, durable human follow-ups after an automatic send is ineligible.

These are REVIEW tasks, not alternate outbound jobs. Never send an Evolution
message or silently fall back to another provider beyond the consent window.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fm_seller.db import Conn

EVOLUTION_OUTSIDE_WINDOW = "evolution_outside_window"
CLOUD_TEMPLATE_UNAVAILABLE = "cloud_template_unavailable"
REASONS = frozenset({EVOLUTION_OUTSIDE_WINDOW, CLOUD_TEMPLATE_UNAVAILABLE})


def enqueue_review(
    conn: Conn,
    *,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    step_id: uuid.UUID,
    reason: str,
    at: datetime,
) -> None:
    """Idempotent; keys and tenant are protected by composite foreign keys and RLS."""
    if reason not in REASONS:
        raise ValueError("Invalid follow-up reason")
    conn.execute(
        "INSERT INTO recovery_followups (tenant_id, case_id, step_id, reason, created_at) "
        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (tenant_id, step_id) DO NOTHING",
        (tenant_id, case_id, step_id, reason, at),
    )


def cancel_case_reviews(conn: Conn, case_id: uuid.UUID, at: datetime) -> None:
    """A purchase, lost outcome, opt-out or superseding case invalidates review tasks."""
    conn.execute(
        "UPDATE recovery_followups SET status='canceled', closed_at=%s "
        "WHERE case_id=%s AND status='pending'",
        (at, case_id),
    )
