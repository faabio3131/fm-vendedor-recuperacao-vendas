from __future__ import annotations

from fastapi import Request

from fm_seller.db import Database
from fm_seller.services import ConnectionService, Principal, resolve_principal

COOKIE = "fm_session"


def get_db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_connection_service(request: Request) -> ConnectionService:
    service: ConnectionService = request.app.state.connections
    return service


def get_principal(request: Request) -> Principal:
    return resolve_principal(
        get_db(request), request.cookies.get(COOKIE), request.headers.get("x-tenant-id")
    )
