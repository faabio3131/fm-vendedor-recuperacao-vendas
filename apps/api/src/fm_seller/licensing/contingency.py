"""Contingência limitada: o que vale quando a licença passou do prazo sem licença nova do Command.

Regras aprovadas (ADR-0005, diretriz 6):
- vale a **última licença validada**; dentro da vigência (ou da carência, em `past_due`) segue
  normal;
- passou do fim sem licença nova: há **uma** janela de no máximo 72 h, que começa no fim da
  vigência (não na hora em que o worker notou), é **persistida** (reiniciar o processo não a
  estende) e **não renova sozinha**: ao acabar o estado vira `suspended` (nada é apagado);
- só uma licença nova validada (versão maior, por evento ou reconciliação) reabre.
`may_serve_during_outage` espelha, regra a regra, `mayServeDuringOutage` do Command (PR #62), para
os dois lados decidirem igual.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.provisioning import lifecycle
from fm_seller.services import audit

log = logging.getLogger("fm_seller.licensing")
MAX_CONTINGENCY = timedelta(hours=72)
_NEVER_SERVE = ("pending", "suspended", "canceled", "refunded", "expired")


@dataclass(frozen=True)
class Decision:
    action: str  # ok | start | serve | exhaust | skip
    started_at: datetime | None = None
    until: datetime | None = None


def normal_end(state: str, valid_until: datetime, grace_ends_at: datetime | None) -> datetime:
    return grace_ends_at if state == "past_due" and grace_ends_at is not None else valid_until


def decide(link: Mapping[str, Any], now: datetime, cap: timedelta) -> Decision:
    """Decisão para um vínculo `fmcommand` (função pura: o relógio entra por parâmetro)."""
    cap = min(cap, MAX_CONTINGENCY)
    if link["state"] not in ("active", "past_due"):
        return Decision("skip")
    end = normal_end(link["state"], link["valid_until"], link["grace_ends_at"])
    if now <= end:
        return Decision("ok")
    same_version = link["contingency_version"] == link["license_version"]
    if same_version and link["contingency_exhausted_at"] is not None:
        return Decision("skip")  # janela desta versão já usada: não renova
    if same_version and link["contingency_started_at"] and link["contingency_until"]:
        started, until = link["contingency_started_at"], link["contingency_until"]
        action = "serve"
    else:
        started, until = end, end + cap
        action = "start"
    if now <= until:
        return Decision(action, started, until)
    return Decision("exhaust", started, until)


def may_serve_during_outage(
    *,
    state: str,
    valid_until: datetime,
    grace_ends_at: datetime | None,
    now: datetime,
    last_validated_at: datetime,
    contingency_started_at: datetime | None,
    contingency_until: datetime | None,
) -> bool:
    """Paridade com `mayServeDuringOutage` do Command: falha fechado em qualquer dúvida."""
    if state in _NEVER_SERVE:
        return False
    if last_validated_at > now:
        return False
    end = normal_end(state, valid_until, grace_ends_at)
    if now <= end:
        return True
    if contingency_started_at is None or contingency_until is None:
        return False
    return (
        contingency_started_at >= end
        and contingency_until > contingency_started_at
        and contingency_until - contingency_started_at <= MAX_CONTINGENCY
        and now <= contingency_until
    )


def enforce_licenses(
    db: Database, settings: Settings, *, now: datetime | None = None
) -> dict[str, int]:
    """Abre, mantém e encerra janelas de contingência. Só no modo `enforce`."""
    counts = {"iniciadas": 0, "encerradas": 0}
    if settings.fmcommand_mode != "enforce":
        return counts
    stamp = now or datetime.now(UTC)
    cap = timedelta(hours=settings.fmcommand_contingency_hours)
    with db.tx(system=True) as conn:
        rows = conn.execute(
            "SELECT * FROM commercial_links WHERE authority = 'fmcommand' "
            "AND state IN ('active', 'past_due') AND "
            "(CASE WHEN state = 'past_due' AND grace_ends_at IS NOT NULL "
            " THEN grace_ends_at ELSE valid_until END) < %s FOR UPDATE",
            (stamp,),
        ).fetchall()
        for link in rows:
            tenant_id: uuid.UUID = link["tenant_id"]
            decision = decide(link, stamp, cap)
            if decision.action == "start":
                conn.execute(
                    "UPDATE commercial_links SET contingency_version = license_version, "
                    "contingency_started_at = %s, contingency_until = %s, "
                    "contingency_exhausted_at = NULL, updated_at = %s WHERE tenant_id = %s",
                    (decision.started_at, decision.until, stamp, tenant_id),
                )
                audit(
                    conn,
                    tenant_id=tenant_id,
                    actor=None,
                    action="license.contingency_started",
                    target=str(link["license_id"]),
                    detail={
                        "until": decision.until.isoformat() if decision.until else None,
                        "version": link["license_version"],
                    },
                )
                counts["iniciadas"] += 1
            elif decision.action == "exhaust":
                conn.execute(
                    "UPDATE commercial_links SET contingency_version = license_version, "
                    "contingency_started_at = %s, contingency_until = %s, "
                    "contingency_exhausted_at = %s, updated_at = %s WHERE tenant_id = %s",
                    (decision.started_at, decision.until, stamp, stamp, tenant_id),
                )
                lifecycle.set_status(
                    conn,
                    tenant_id,
                    lifecycle.SUSPENDED,
                    target="contingencia_esgotada",
                    now=stamp,
                )
                audit(
                    conn,
                    tenant_id=tenant_id,
                    actor=None,
                    action="license.contingency_exhausted",
                    target=str(link["license_id"]),
                    detail={"version": link["license_version"]},
                )
                counts["encerradas"] += 1
    return counts
