"""Primeiros passos: o que falta para atender e vender. Qualquer papel pode ver."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from fm_seller import onboarding
from fm_seller.api.deps import get_db, get_principal
from fm_seller.services import Principal

router = APIRouter(tags=["primeiros-passos"])


@router.get("/onboarding")
def get_onboarding(
    request: Request, p: Annotated[Principal, Depends(get_principal)]
) -> dict[str, Any]:
    with get_db(request).tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
        return onboarding.overview(conn, p.tenant_id)
