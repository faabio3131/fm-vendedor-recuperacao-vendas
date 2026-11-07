"""Jornada QR do AtendeVendeIA. Nunca serve credenciais da Evolution ao navegador."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, Response

from fm_seller.api.deps import get_principal
from fm_seller.providers.evolution_pairing import EvolutionPairingService
from fm_seller.services import Principal

router = APIRouter(prefix="/connections/evolution", tags=["Evolution / WhatsApp"])


@router.get("/status")
def status(request: Request, p: Annotated[Principal, Depends(get_principal)]) -> dict[str, Any]:
    service = cast(EvolutionPairingService, request.app.state.evolution_pairing)
    return service.status(p)


@router.post("/connect")
def connect(request: Request, p: Annotated[Principal, Depends(get_principal)]) -> dict[str, Any]:
    service = cast(EvolutionPairingService, request.app.state.evolution_pairing)
    return service.connect(p)


@router.get("/qr")
def refresh_qr(
    request: Request, response: Response, p: Annotated[Principal, Depends(get_principal)]
) -> dict[str, Any]:
    """Atualiza QR sem recriar instância, reconfigurar webhook ou registrar segredo."""
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    service = cast(EvolutionPairingService, request.app.state.evolution_pairing)
    return service.refresh_qr(p)
