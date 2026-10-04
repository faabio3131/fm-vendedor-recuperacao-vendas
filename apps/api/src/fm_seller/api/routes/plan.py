"""Tela "Meu plano": estado da assinatura, carência e uso. Qualquer papel pode ver."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from fm_seller.api.deps import get_db, get_principal
from fm_seller.provisioning import lifecycle
from fm_seller.services import Principal

router = APIRouter(tags=["plano"])


@router.get("/plan")
def plan(request: Request, p: Annotated[Principal, Depends(get_principal)]) -> dict[str, Any]:
    with get_db(request).tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
        return lifecycle.overview(conn, p.tenant_id)
