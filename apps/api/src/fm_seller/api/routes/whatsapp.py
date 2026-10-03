"""Webhook do WhatsApp Cloud: verificação (GET) e eventos (POST). Sem cookie."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from fm_seller.channels.whatsapp import ingest_whatsapp, verify_handshake

router = APIRouter(prefix="/webhooks/whatsapp_cloud", tags=["whatsapp"])


@router.get("/{public_id}", response_class=PlainTextResponse)
def verify(
    public_id: str,
    request: Request,
    mode: Annotated[str, Query(alias="hub.mode")] = "",
    token: Annotated[str, Query(alias="hub.verify_token")] = "",
    challenge: Annotated[str, Query(alias="hub.challenge")] = "",
) -> str:
    return verify_handshake(
        request.app.state.db, request.app.state.box, public_id, mode, token, challenge
    )


@router.post("/{public_id}")
async def receive(public_id: str, request: Request) -> dict[str, int]:
    raw = await request.body()
    return ingest_whatsapp(
        request.app.state.db,
        request.app.state.box,
        public_id=public_id,
        headers=request.headers,
        raw_body=raw,
    )
