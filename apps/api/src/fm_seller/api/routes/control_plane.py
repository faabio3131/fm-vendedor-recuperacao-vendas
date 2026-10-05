"""Conexão com o FM Command: leitura agregada por token de serviço. Fora de qualquer rota de pessoa
ou de cliente; ver `control_plane.py` para as cercas e o ADR-0004."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, Response

from fm_seller import control_plane as cp
from fm_seller import platform_admin as pa
from fm_seller.api.deps import get_db
from fm_seller.recovery.senders import build_sender

router = APIRouter(prefix="/control-plane/fmcc", tags=["fm-command"])


@router.get("/health")
def health(
    request: Request,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """200 = saudável; 503 = banco fora ou achado crítico. Só códigos, sem dado de cliente."""
    cfg = request.app.state.settings
    cp.authorize(cfg, authorization)
    db = get_db(request)
    if not db.ping():
        response.status_code = 503
        return {"schema_version": cp.SCHEMA_VERSION, "status": "database_unavailable"}
    report = pa.health_findings(db, build_sender(cfg))
    degraded = report["exit_code"] == 2
    if degraded:
        response.status_code = 503
    return {
        "schema_version": cp.SCHEMA_VERSION,
        "product_code": cp.PRODUCT_CODE,
        "status": "degraded" if degraded else "ok",
        "findings": [{"level": f["level"], "code": f["code"]} for f in report["findings"]],
    }


@router.get("/snapshot")
def snapshot(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    cp.authorize(request.app.state.settings, authorization)
    with get_db(request).tx(system=True) as conn:
        return cp.build_snapshot(conn)
