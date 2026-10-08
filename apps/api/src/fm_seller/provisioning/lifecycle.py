"""Ciclo de vida da assinatura de quem comprou o AtendeVendeIA na Cakto/Hotmart.

Estados (`tenant_plans.status`):
- `active`: tudo liberado.
- `past_due`: pagamento em atraso, ainda dentro da carência (`plans.grace_days`); tudo liberado.
- `suspended`: carência acabou (ou suspensão manual); envios e vendedor IA pausados.
- `canceled` / `refunded`: idem; reembolso e estorno seguem a regra da plataforma (D5).

Nada é apagado ao suspender: reativar devolve o acesso com os mesmos dados. A ordem dos eventos
não é confiável (a plataforma pode reenviar ou atrasar), então cada evento só muda o estado nas
transições previstas em `next_status`; o resto é ignorado e fica registrado.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fm_seller.ai import usage
from fm_seller.db import Conn, Database
from fm_seller.events import normalize as n
from fm_seller.services import audit

ACTIVE = "active"
PAST_DUE = "past_due"
SUSPENDED = "suspended"
CANCELED = "canceled"
REFUNDED = "refunded"
STATUSES = (ACTIVE, PAST_DUE, SUSPENDED, CANCELED, REFUNDED)
BLOCKING = (SUSPENDED, CANCELED, REFUNDED)
ACTIVATING = (n.PURCHASE_APPROVED, n.SUBSCRIPTION_ACTIVE)


def blocks_service(status: str | None) -> bool:
    """True se o plano pausa envios e vendedor IA. Sem plano registrado não bloqueia."""
    return status in BLOCKING


def next_status(current: str, kind: str) -> str | None:
    """Estado seguinte para o evento, ou None se o evento não muda nada neste estado."""
    if kind in ACTIVATING:
        return ACTIVE
    if kind == n.SUBSCRIPTION_LATE:
        return PAST_DUE if current == ACTIVE else None
    if kind == n.SUBSCRIPTION_RECOVERED:
        return ACTIVE if current in (PAST_DUE, SUSPENDED) else None
    if kind == n.SUBSCRIPTION_CANCELED:
        return CANCELED if current in (ACTIVE, PAST_DUE, SUSPENDED) else None
    if kind == n.REFUNDED:
        return None if current == REFUNDED else REFUNDED
    return None


def set_status(
    conn: Conn,
    tenant_id: uuid.UUID,
    new: str,
    *,
    target: str,
    actor: uuid.UUID | None = None,
    now: datetime | None = None,
) -> None:
    """Muda o estado, marca desde quando, guarda o início do atraso e registra na auditoria."""
    assert new in STATUSES
    stamp = now or datetime.now(UTC)
    conn.execute(
        "UPDATE tenant_plans SET status_since = CASE WHEN status <> %s THEN %s ELSE status_since "
        "END, past_due_since = CASE WHEN %s = 'past_due' THEN coalesce(past_due_since, %s) "
        "ELSE NULL END, status = %s WHERE tenant_id = %s",
        (new, stamp, new, stamp, new, tenant_id),
    )
    audit(conn, tenant_id=tenant_id, actor=actor, action=f"plan.{new}", target=target)


def apply_event(conn: Conn, tenant_id: uuid.UUID, kind: str, email: str) -> bool:
    """Aplica o evento ao plano do cliente. Devolve True se o estado mudou."""
    row = conn.execute(
        "SELECT status FROM tenant_plans WHERE tenant_id = %s FOR UPDATE", (tenant_id,)
    ).fetchone()
    if row is None:
        return False
    new = next_status(row["status"], kind)
    if new is None or new == row["status"]:
        return False
    set_status(conn, tenant_id, new, target=email)
    return True


def enforce_grace(db: Database, *, now: datetime | None = None) -> int:
    """Suspende quem passou da carência em atraso. Devolve quantos foram suspensos."""
    stamp = now or datetime.now(UTC)
    with db.tx(system=True) as conn:
        rows = conn.execute(
            "SELECT tp.tenant_id FROM tenant_plans tp JOIN plans p ON p.key = tp.plan_key "
            "WHERE tp.status = 'past_due' AND tp.past_due_since IS NOT NULL "
            "AND tp.past_due_since + make_interval(days => p.grace_days) <= %s "
            # A carência de quem o Command governa é do Command (grace_ends_at da licença).
            "AND NOT EXISTS (SELECT 1 FROM commercial_links cl WHERE cl.tenant_id = tp.tenant_id "
            "AND cl.authority = 'fmcommand') FOR UPDATE OF tp",
            (stamp,),
        ).fetchall()
        for r in rows:
            set_status(conn, r["tenant_id"], SUSPENDED, target="carencia_esgotada", now=stamp)
    return len(rows)


def set_plan_status(
    db: Database, tenant_id: uuid.UUID, new: str, *, actor: uuid.UUID | None = None
) -> bool:
    """Suspensão ou reativação manual (operação). Devolve False se o cliente não tem plano."""
    with db.tx(system=True) as conn:
        row = conn.execute(
            "SELECT status FROM tenant_plans WHERE tenant_id = %s FOR UPDATE", (tenant_id,)
        ).fetchone()
        if row is None:
            return False
        if row["status"] != new:
            set_status(conn, tenant_id, new, target="manual", actor=actor)
    return True


def overview(conn: Conn, tenant_id: uuid.UUID, now: datetime | None = None) -> dict[str, Any]:
    """Dados da tela "Meu plano". Só o que o sistema sabe: nada inventado sobre a cobrança."""
    stamp = now or datetime.now(UTC)
    row = conn.execute(
        "SELECT tp.status, tp.plan_key, tp.source, tp.started_at, tp.status_since, "
        "tp.past_due_since, p.display_name, p.grace_days FROM tenant_plans tp "
        "JOIN plans p ON p.key = tp.plan_key WHERE tp.tenant_id = %s",
        (tenant_id,),
    ).fetchone()
    if row is None:
        return {"plan": None, "state": "none"}
    settings = conn.execute(
        "SELECT timezone, number_daily_limit FROM tenant_settings WHERE tenant_id = %s",
        (tenant_id,),
    ).fetchone()
    tz = settings["timezone"] if settings else "America/Sao_Paulo"
    status = row["status"]
    grace_ends = None
    if status == PAST_DUE and row["past_due_since"] is not None:
        grace_ends = row["past_due_since"] + timedelta(days=row["grace_days"])
    state = "blocked" if blocks_service(status) else ("grace" if status == PAST_DUE else "active")
    return {
        "plan": {"key": row["plan_key"], "name": row["display_name"]},
        "status": status,
        "state": state,
        "source": row["source"],
        "started_at": row["started_at"],
        "status_since": row["status_since"],
        "grace_days": row["grace_days"],
        "grace_ends_at": grace_ends,
        "grace_days_left": None
        if grace_ends is None
        else max(0, -(-int((grace_ends - stamp).total_seconds()) // 86400)),
        "ai": usage.summary(conn, tenant_id, tz),
        "number_daily_limit": settings["number_daily_limit"] if settings else 200,
    }
