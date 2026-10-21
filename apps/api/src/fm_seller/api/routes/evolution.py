"""Jornada QR do AtendeVendeIA. Nunca serve credenciais da Evolution ao navegador."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from fm_seller.api.deps import get_principal
from fm_seller.services import Principal

router = APIRouter(prefix="/connections/evolution", tags=["Evolution / WhatsApp"])


@router.get("/status")
def status(request: Request, p: Annotated[Principal, Depends(get_principal)]) -> dict[str, Any]:
    return request.app.state.evolution_pairing.status(p)


@router.post("/connect")
def connect(request: Request, p: Annotated[Principal, Depends(get_principal)]) -> dict[str, Any]:
    return request.app.state.evolution_pairing.connect(p)
