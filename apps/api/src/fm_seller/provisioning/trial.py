"""Governed trial on canonical tenant_plans, with one send reservation per message.

Only trusted platform/internal send paths call reserve/finalize using system transactions.
RLS-scoped tenant reads may inspect the snapshot but cannot create or extend trials.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fm_seller.db import Conn, Database
from fm_seller.errors import bad_request
from fm_seller.services import audit

PLAN = "av-trial"
SOURCE_KINDS = ("bot", "recovery")


def snapshot(
    conn: Conn, tenant_id: uuid.UUID, *, now: datetime | None = None
) -> dict[str, Any] | None:
    """Read-only trial state from the same plan authority used for paid subscriptions."""
    stamp = now or datetime.now(UTC)
    row = conn.execute(
        "SELECT plan_key, trial_started_at, trial_expires_at, trial_message_cap, "
        "trial_warn_at, trial_messages_sent, trial_messages_reserved "
        "FROM tenant_plans WHERE tenant_id = %s",
        (tenant_id,),
    ).fetchone()
    if row is None or row["plan_key"] != PLAN:
        return None
    started, ends, cap, threshold = (
        row["trial_started_at"],
        row["trial_expires_at"],
        row["trial_message_cap"],
        row["trial_warn_at"],
    )
    used = int(row["trial_messages_sent"])
    reserved = int(row["trial_messages_reserved"])
    # A trial plan without authoritative dates or limits is always closed.
    valid = (
        started is not None and ends is not None and isinstance(cap, int)
        and cap > 0 and isinstance(threshold, int) and 0 < threshold < cap
    )
    expired = not valid or stamp >= ends
    exhausted = not valid or used + reserved >= cap
    return {
        "status": "expired" if expired else "exhausted" if exhausted else "active",
        "started_at": started,
        "expires_at": ends,
        "limit": cap,
        "used": used,
        "reserved": reserved,
        "remaining": max(0, (cap or 0) - used - reserved),
        "warn_at": threshold,
        "conversion_prompt": valid and (used >= threshold or expired or exhausted),
    }


def blocked(conn: Conn, tenant_id: uuid.UUID, *, now: datetime | None = None) -> bool:
    current = snapshot(conn, tenant_id, now=now)
    return current is not None and current["status"] != "active"


def start(
    db: Database, tenant_id: uuid.UUID, *, actor: uuid.UUID, now: datetime | None = None
) -> dict[str, Any]:
    """Internal platform operation only. Never reset an earlier trial."""
    stamp = now or datetime.now(UTC)
    with db.tx(system=True, user_id=actor) as conn:
        row = conn.execute(
            "SELECT plan_key, trial_started_at FROM tenant_plans WHERE tenant_id = %s FOR UPDATE",
            (tenant_id,),
        ).fetchone()
        if row is None:
            raise bad_request("no_plan", "Cliente sem plano para iniciar o teste.")
        if row["trial_started_at"] is not None or row["plan_key"] == PLAN:
            raise bad_request("trial_used", "Este cliente já recebeu um teste grátis.")
        if row["plan_key"] not in ("fase-1",):
            raise bad_request("paid_plan", "Não substituir uma assinatura comercial pelo trial.")
        policy = conn.execute(
            "SELECT duration_days, message_cap, warn_at FROM trial_policy WHERE singleton"
        ).fetchone()
        if policy is None:
            raise bad_request("trial_policy_missing", "Política do trial não configurada.")
        conn.execute(
            "UPDATE tenant_plans SET plan_key = %s, trial_started_at = %s, "
            "trial_expires_at = %s, trial_message_cap = %s, trial_warn_at = %s "
            "WHERE tenant_id = %s",
            (
                PLAN, stamp, stamp + timedelta(days=int(policy["duration_days"])),
                policy["message_cap"], policy["warn_at"], tenant_id,
            ),
        )
        audit(conn, tenant_id=tenant_id, actor=actor, action="trial.started", target=PLAN)
        current = snapshot(conn, tenant_id, now=stamp)
        assert current is not None
        return current


def reserve(
    db: Database,
    tenant_id: uuid.UUID,
    *,
    source_kind: str,
    source_id: uuid.UUID,
    now: datetime | None = None,
) -> str:
    """Return paid, reserved, duplicate or blocked; only 'paid'/'reserved' permit a send.

    Row-level serialization ensures all workers and both send pipelines share one cap.
    """
    if source_kind not in SOURCE_KINDS:
        raise ValueError("source_kind inválido")
    stamp = now or datetime.now(UTC)
    with db.tx(system=True) as conn:
        row = conn.execute(
            "SELECT plan_key, trial_expires_at, trial_message_cap, "
            "trial_messages_sent, trial_messages_reserved FROM tenant_plans "
            "WHERE tenant_id = %s FOR UPDATE",
            (tenant_id,),
        ).fetchone()
        if row is None:
            return "blocked"
        if row["plan_key"] != PLAN:
            return "paid"
        duplicate = conn.execute(
            "SELECT status FROM trial_message_ledger "
            "WHERE tenant_id = %s AND source_kind = %s AND source_id = %s",
            (tenant_id, source_kind, source_id),
        ).fetchone()
        if duplicate is not None:
            return "duplicate"
        cap, expires = row["trial_message_cap"], row["trial_expires_at"]
        if (
            expires is None or stamp >= expires or not isinstance(cap, int)
            or row["trial_messages_sent"] + row["trial_messages_reserved"] >= cap
        ):
            return "blocked"
        conn.execute(
            "INSERT INTO trial_message_ledger (tenant_id, source_kind, source_id, status) "
            "VALUES (%s, %s, %s, 'reserved')",
            (tenant_id, source_kind, source_id),
        )
        conn.execute(
            "UPDATE tenant_plans SET trial_messages_reserved = trial_messages_reserved + 1 "
            "WHERE tenant_id = %s",
            (tenant_id,),
        )
        return "reserved"


def finalize(
    db: Database,
    tenant_id: uuid.UUID,
    *,
    source_kind: str,
    source_id: uuid.UUID,
    outcome: str,
) -> bool:
    """Finalize once. 'uncertain' consumes capacity to avoid accidental over-issuance."""
    if outcome not in ("sent", "failed", "uncertain"):
        raise ValueError("outcome inválido")
    with db.tx(system=True) as conn:
        conn.execute(
            "SELECT 1 FROM tenant_plans WHERE tenant_id = %s FOR UPDATE",
            (tenant_id,),
        )
        row = conn.execute(
            "SELECT status FROM trial_message_ledger "
            "WHERE tenant_id = %s AND source_kind = %s AND source_id = %s FOR UPDATE",
            (tenant_id, source_kind, source_id),
        ).fetchone()
        if row is None or row["status"] != "reserved":
            return False
        conn.execute(
            "UPDATE trial_message_ledger SET status = %s, finalized_at = now() "
            "WHERE tenant_id = %s AND source_kind = %s AND source_id = %s",
            (outcome, tenant_id, source_kind, source_id),
        )
        consumed = outcome != "failed"
        conn.execute(
            "UPDATE tenant_plans SET trial_messages_reserved = trial_messages_reserved - 1, "
            "trial_messages_sent = trial_messages_sent + %s WHERE tenant_id = %s",
            (1 if consumed else 0, tenant_id),
        )
        return True
