"""Administração da plataforma (equipe da F&M). Fora de qualquer rota de cliente: ver
`platform_admin.py` para as cercas. Nunca devolve conversa, contato nem credencial."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from fm_seller import platform_admin as pa
from fm_seller.api.deps import COOKIE, get_db
from fm_seller.recovery.senders import build_sender

router = APIRouter(prefix="/admin", tags=["plataforma"])


def get_admin(request: Request) -> pa.Admin:
    return pa.require_admin(get_db(request), request.cookies.get(COOKIE))


Who = Annotated[pa.Admin, Depends(get_admin)]


class PlanIn(BaseModel):
    plan: str = Field(min_length=1, max_length=40)


@router.get("/me")
def me(admin: Who) -> dict[str, str]:
    return {"email": admin.email}


@router.get("/clients")
def clients(
    request: Request,
    _: Who,
    limit: Annotated[int, Query(ge=1, le=pa.PAGE_MAX)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    with get_db(request).tx(system=True) as conn:
        return pa.list_clients(conn, limit=limit, offset=offset)


@router.get("/metrics")
def metrics(request: Request, _: Who) -> dict[str, Any]:
    with get_db(request).tx(system=True) as conn:
        return pa.metrics(conn)


@router.get("/health")
def health(request: Request, _: Who) -> dict[str, Any]:
    sender = build_sender(request.app.state.settings)
    return pa.health_findings(get_db(request), sender)


@router.post("/clients/{tenant_id}/suspend")
def suspend(tenant_id: uuid.UUID, request: Request, admin: Who) -> dict[str, Any]:
    return pa.set_plan_status(get_db(request), admin, tenant_id, "suspend")


@router.post("/clients/{tenant_id}/reactivate")
def reactivate(tenant_id: uuid.UUID, request: Request, admin: Who) -> dict[str, Any]:
    return pa.set_plan_status(get_db(request), admin, tenant_id, "reactivate")


@router.put("/clients/{tenant_id}/plan")
def change_plan(tenant_id: uuid.UUID, body: PlanIn, request: Request, admin: Who) -> dict[str, Any]:
    return pa.change_plan(get_db(request), admin, tenant_id, body.plan)


@router.post("/clients/{tenant_id}/resend-invite")
def resend_invite(tenant_id: uuid.UUID, request: Request, admin: Who) -> dict[str, Any]:
    return pa.resend_invite(get_db(request), admin, tenant_id)
