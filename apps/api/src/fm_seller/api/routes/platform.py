"""Compras do próprio SaaS. Sem cookie: a autenticação é o segredo do webhook."""

from __future__ import annotations

from fastapi import APIRouter, Request

from fm_seller.licensing.service import ingest_webhook
from fm_seller.provisioning.platform import ingest_platform_event

router = APIRouter(prefix="/platform/webhooks", tags=["platform"])


# Licenças do Billing Central (ADR-0005). Rota específica ANTES da genérica: Cakto e Hotmart seguem
# pelo caminho de sempre, sem nenhuma mudança. Desligada (404) enquanto FM_FMCOMMAND_MODE=off.
@router.post("/fmcommand")
async def receive_license(request: Request) -> dict[str, str]:
    raw = await request.body()
    outcome = ingest_webhook(
        request.app.state.db,
        request.app.state.box,
        request.app.state.settings,
        headers=dict(request.headers),
        raw_body=raw,
    )
    return {"status": outcome}


@router.post("/{provider}")
async def receive(provider: str, request: Request) -> dict[str, str]:
    raw = await request.body()
    outcome = ingest_platform_event(
        request.app.state.db,
        request.app.state.box,
        request.app.state.settings,
        provider=provider,
        headers=request.headers,
        raw_body=raw,
    )
    return {"status": outcome}
