"""Webhook Evolution: authenticated per tenant+instance; no session or Origin header."""

from __future__ import annotations

from fastapi import APIRouter, Request

from fm_seller.channels.evolution_runtime import ingest_evolution

router = APIRouter(prefix="/webhooks/evolution", tags=["Evolution / WhatsApp"])


@router.post("/{public_id}")
async def receive(public_id: str, request: Request) -> dict[str, int]:
    raw = await request.body()
    return ingest_evolution(
        request.app.state.db,
        request.app.state.box,
        request.app.state.settings,
        public_id=public_id,
        headers={k.lower(): v for k, v in request.headers.items()},
        raw_body=raw,
    )
