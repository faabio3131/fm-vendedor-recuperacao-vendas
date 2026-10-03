"""Fábrica da aplicação FastAPI. Dependências entram por parâmetro (testável sem rede)."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from fm_seller import __version__
from fm_seller.api.routes import auth, connections, health, me, platform, recovery, webhooks
from fm_seller.auth.google import GoogleVerifier, build_verifier
from fm_seller.config import Settings, get_settings
from fm_seller.db import Database
from fm_seller.errors import AppError
from fm_seller.events.ingest import EventHandler
from fm_seller.logging_setup import setup_logging
from fm_seller.providers.testers import ConnectionTester, build_tester
from fm_seller.recovery.engine import handle_event
from fm_seller.security.crypto import SecretBox
from fm_seller.services import ConnectionService

log = logging.getLogger("fm_seller.http")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    verifier: GoogleVerifier | None = None,
    tester: ConnectionTester | None = None,
    box: SecretBox | None = None,
    event_handler: EventHandler | None = None,
) -> FastAPI:
    cfg = settings or get_settings()
    setup_logging()
    database = db or Database(cfg.database_url)
    secret_box = box or SecretBox(cfg.secrets_keys)
    google = verifier or build_verifier(cfg.env, cfg.google_client_id)
    conn_tester = tester or build_tester(cfg.env)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if db is None:
            database.open()
        yield
        if db is None:
            database.close()

    app = FastAPI(
        title="F&M Vendedor e Recuperação de Vendas", version=__version__, lifespan=lifespan
    )
    app.state.settings = cfg
    app.state.db = database
    app.state.verifier = google
    app.state.connections = ConnectionService(database, cfg, secret_box, conn_tester)
    app.state.box = secret_box
    app.state.event_handler = event_handler or handle_event

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[cfg.web_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-Tenant-Id"],
    )

    @app.middleware("http")
    async def request_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        started = time.perf_counter()
        if request.method in UNSAFE and not request.url.path.startswith(
            ("/v1/webhooks/", "/v1/platform/webhooks/")
        ):
            origin = request.headers.get("origin")
            if origin != cfg.web_origin:
                response: Response = JSONResponse(
                    status_code=403,
                    content={"error": {"code": "bad_origin", "message": "Origem não permitida."}},
                )
                response.headers["x-request-id"] = request_id
                return response
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        log.info(
            "request",
            extra={
                "ctx": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                }
            },
        )
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}}
        )

    app.include_router(health.router, prefix="/v1")
    app.include_router(auth.router, prefix="/v1")
    app.include_router(me.router, prefix="/v1")
    app.include_router(connections.router, prefix="/v1")
    app.include_router(webhooks.router, prefix="/v1")
    app.include_router(platform.router, prefix="/v1")
    app.include_router(recovery.router, prefix="/v1")
    return app
