"""Relatórios de recuperação (JSON e CSV). Só números do próprio cliente."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse

from fm_seller.api.deps import get_db, get_principal
from fm_seller.errors import AppError
from fm_seller.recovery import reports
from fm_seller.services import Principal, tenant_features

router = APIRouter(prefix="/reports", tags=["relatórios"])
Who = Annotated[Principal, Depends(get_principal)]
FEATURE = "recovery.sequences"


def _build(
    request: Request, p: Principal, start: date | None, end: date | None, group_by: str
) -> dict[str, Any]:
    db = get_db(request)
    if FEATURE not in tenant_features(db, p)[2]:
        raise AppError(403, "plan_required", "Seu plano atual não inclui recuperação de vendas.")
    with db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
        found = conn.execute(
            "SELECT timezone FROM tenant_settings WHERE tenant_id = %s", (p.tenant_id,)
        ).fetchone()
        tz = found["timezone"] if found else "America/Sao_Paulo"
        s, e = reports.parse_period(tz, start, end)
        return reports.build(conn, p.tenant_id, tz, s, e, group_by)


@router.get("/recovery")
def recovery(
    request: Request,
    p: Who,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
    group_by: str = "none",
) -> dict[str, Any]:
    return _build(request, p, start, end, group_by)


@router.get("/recovery.csv", response_class=PlainTextResponse)
def recovery_csv(
    request: Request,
    p: Who,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
    group_by: str = "none",
) -> PlainTextResponse:
    report = _build(request, p, start, end, group_by)
    name = f"recuperacao_{report['from']}_{report['to']}.csv"
    return PlainTextResponse(
        reports.to_csv(report),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
