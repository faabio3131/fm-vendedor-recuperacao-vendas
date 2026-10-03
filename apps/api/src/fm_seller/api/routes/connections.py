from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from fm_seller.api.deps import get_connection_service, get_principal
from fm_seller.services import ConnectionService, Principal

router = APIRouter(prefix="/connections", tags=["conexões"])

Svc = Annotated[ConnectionService, Depends(get_connection_service)]
Who = Annotated[Principal, Depends(get_principal)]


class ConnectionIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict, max_length=20)


@router.get("")
def list_connections(svc: Svc, p: Who) -> list[dict[str, Any]]:
    return svc.list(p)


@router.put("/{provider}")
def put_connection(provider: str, body: ConnectionIn, svc: Svc, p: Who) -> dict[str, Any]:
    return svc.put(p, provider, body.values)


@router.post("/{provider}/test")
def test_connection(provider: str, svc: Svc, p: Who) -> dict[str, Any]:
    return svc.test(p, provider)


@router.delete("/{provider}")
def delete_connection(provider: str, svc: Svc, p: Who) -> dict[str, str]:
    svc.delete(p, provider)
    return {"status": "removed"}
