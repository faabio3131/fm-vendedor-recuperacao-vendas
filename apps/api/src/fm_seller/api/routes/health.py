from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from fm_seller import __version__

router = APIRouter(tags=["saúde"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/ready")
def ready(request: Request, response: Response) -> dict[str, Any]:
    ok: bool = request.app.state.db.ping()
    if not ok:
        response.status_code = 503
    return {"status": "ready" if ok else "database_unavailable"}
