"""Privacidade (LGPD): retenção, consentimento, dados de um contato e exclusão da conta."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from fm_seller import privacy
from fm_seller.api.deps import get_db, get_principal
from fm_seller.errors import bad_request
from fm_seller.services import Principal

router = APIRouter(tags=["privacidade"])
Who = Annotated[Principal, Depends(get_principal)]
CONFIRM_DELETE = "EXCLUIR"


class RetentionIn(BaseModel):
    retention_days: int


class ContactIn(BaseModel):
    identifier: str = Field(max_length=200)


class EraseIn(ContactIn):
    confirm: bool = False
    also_block: bool = False


class DeletionIn(BaseModel):
    confirm: str = Field(default="", max_length=20)


def _grace(request: Request) -> int:
    return int(request.app.state.settings.tenant_deletion_grace_days)


@router.get("/privacy")
def overview(request: Request, p: Who) -> dict[str, Any]:
    with get_db(request).tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
        return privacy.overview(conn, p, _grace(request))


@router.put("/privacy/retention")
def retention(body: RetentionIn, request: Request, p: Who) -> dict[str, int]:
    with get_db(request).tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
        return {"retention_days": privacy.set_retention(conn, p, body.retention_days)}


@router.post("/privacy/contacts/export")
def export_contact(body: ContactIn, request: Request, p: Who) -> Any:
    with get_db(request).tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
        data = privacy.export_contact(conn, p, body.identifier)
    if data is None:
        return {"found": False}
    return JSONResponse({"found": True, "data": data}, headers={"Cache-Control": "no-store"})


@router.post("/privacy/contacts/erase")
def erase_contact(body: EraseIn, request: Request, p: Who) -> dict[str, Any]:
    if not body.confirm:
        raise bad_request("confirm_required", "Confirme que quer apagar este contato.")
    with get_db(request).tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
        return privacy.erase_contact(conn, p, body.identifier, also_block=body.also_block)


@router.post("/privacy/account/deletion")
def request_deletion(body: DeletionIn, request: Request, p: Who) -> dict[str, Any]:
    if body.confirm != CONFIRM_DELETE:
        raise bad_request("confirm_required", f"Digite {CONFIRM_DELETE} para confirmar.")
    return privacy.request_deletion(get_db(request), p, _grace(request))


@router.delete("/privacy/account/deletion")
def cancel_deletion(request: Request, p: Who) -> dict[str, Any]:
    return privacy.cancel_deletion(get_db(request), p, _grace(request))
