from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from fm_seller.api.deps import COOKIE, get_db
from fm_seller.auth.google import GoogleVerifier
from fm_seller.config import Settings
from fm_seller.services import login_with_google, revoke_session

router = APIRouter(prefix="/auth", tags=["autenticação"])


class GoogleLogin(BaseModel):
    id_token: str = Field(min_length=10, max_length=4096)


@router.post("/google")
def google_login(body: GoogleLogin, request: Request, response: Response) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    verifier: GoogleVerifier = request.app.state.verifier
    identity = verifier.verify(body.id_token)
    token = login_with_google(get_db(request), settings, identity)
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return {"status": "ok"}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, str]:
    token = request.cookies.get(COOKIE)
    if token:
        revoke_session(get_db(request), token)
    response.delete_cookie(COOKIE, path="/")
    return {"status": "ok"}
