"""Eventos reais de checkout capturados para conferência (só dono e administrador do cliente).

O corpo vem mascarado (dados pessoais escondidos, segredos já redigidos na gravação). Só existe
conteúdo aqui se o operador ligou a captura (`FM_CAPTURE_EVENTS`).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from fm_seller.api.deps import get_db, get_principal
from fm_seller.errors import forbidden, not_found
from fm_seller.events import capture, compare
from fm_seller.services import EDIT_ROLES, Principal

router = APIRouter(prefix="/captures", tags=["captura"])
Who = Annotated[Principal, Depends(get_principal)]


def _guard(p: Principal) -> None:
    if p.role not in EDIT_ROLES:
        raise forbidden("Só dono ou administrador vê os eventos capturados.")


@router.get("")
def list_(request: Request, p: Who) -> dict[str, Any]:
    _guard(p)
    cfg = request.app.state.settings
    with get_db(request).tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
        items = capture.list_captures(conn)
    return {"enabled": cfg.capture_events, "ttl_hours": cfg.capture_ttl_hours, "items": items}


@router.get("/{capture_id}")
def detail(capture_id: uuid.UUID, request: Request, p: Who) -> dict[str, Any]:
    _guard(p)
    with get_db(request).tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
        found = capture.load_capture(conn, request.app.state.box, capture_id)
    if found is None:
        raise not_found("Evento não encontrado ou já expirou.")
    return {
        **{k: found[k] for k in ("id", "provider", "event_type", "auth_method")},
        "captured_at": found["captured_at"],
        "expires_at": found["expires_at"],
        "headers": capture.mask(found["headers"]),
        "payload": capture.mask(found["payload"]),
        "comparison": compare.compare(found["provider"], found["payload"]),
    }
