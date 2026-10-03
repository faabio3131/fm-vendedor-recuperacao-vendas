"""Compras do próprio SaaS. Sem cookie: a autenticação é o segredo do webhook."""

from __future__ import annotations

from fastapi import APIRouter, Request

from fm_seller.provisioning.platform import ingest_platform_event

router = APIRouter(prefix="/platform/webhooks", tags=["platform"])


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
