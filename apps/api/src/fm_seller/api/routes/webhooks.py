"""Webhooks recebidos das plataformas de checkout do cliente.

Sem cookie: a autenticação é o segredo.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from fm_seller.events.capture import CaptureConfig
from fm_seller.events.ingest import EventHandler, ingest_webhook

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def capture_config(settings: Any) -> CaptureConfig:
    return CaptureConfig(settings.capture_events, settings.capture_ttl_hours, settings.capture_keep)


@router.post("/{provider}/{public_id}")
async def receive(provider: str, public_id: str, request: Request) -> dict[str, str]:
    raw = await request.body()
    handler: EventHandler = request.app.state.event_handler
    result = ingest_webhook(
        request.app.state.db,
        request.app.state.box,
        provider=provider,
        public_id=public_id,
        headers=request.headers,
        raw_body=raw,
        handler=handler,
        capture=capture_config(request.app.state.settings),
    )
    return {"status": result.status}
