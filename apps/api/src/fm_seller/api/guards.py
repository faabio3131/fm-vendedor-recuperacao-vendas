"""Defesas da borda da API: limite de taxa, tamanho de corpo e cabeçalhos de segurança.

Tudo aqui é independente de rota: a regra olha método e caminho. Ver docs/SEGURANCA.md.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from fm_seller.api.deps import COOKIE
from fm_seller.config import Settings
from fm_seller.security.ratelimit import RateLimiter

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
WEBHOOKS = ("/v1/webhooks/", "/v1/platform/webhooks/")
FAILED_WEBHOOK = {400, 401, 403, 404}
Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


def security_headers(cfg: Settings) -> dict[str, str]:
    """Cabeçalhos de uma API que só devolve JSON: nada embutível, nada interpretado como página."""
    headers = {
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "same-site",
    }
    if cfg.is_production_like:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()[:64] or "desconhecido"
    return request.client.host if request.client else "desconhecido"


def _account_key(request: Request, ip: str) -> str:
    token = request.cookies.get(COOKIE)
    if not token:
        return "ip:" + ip
    return "acct:" + hashlib.sha256(token.encode()).hexdigest()[:24]


def is_sensitive(request: Request) -> bool:
    """Exportação, apagamento, importação e planilha: pedido caro ou que mexe em dado pessoal."""
    path = request.url.path
    if path.startswith("/v1/privacy"):
        return request.method in UNSAFE
    if path.endswith(".csv"):
        return True
    return request.method in UNSAFE and path.endswith("/import")


class Guards:
    def __init__(self, cfg: Settings, limiter: RateLimiter | None = None) -> None:
        self.cfg = cfg
        self.limiter = limiter or RateLimiter()

    def too_many(self, retry_after: int) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "rate_limited",
                    "message": "Muitas tentativas. Tente de novo em alguns instantes.",
                }
            },
            headers={"Retry-After": str(retry_after)},
        )

    def before(self, request: Request) -> JSONResponse | None:
        """Devolve a resposta 429 se o pedido passou do limite; None se pode seguir."""
        cfg = self.cfg
        if not cfg.rate_limit_enabled:
            return None
        path = request.url.path
        ip = client_ip(request, cfg.trust_proxy)
        lim = self.limiter
        if path.startswith(WEBHOOKS):
            fail_key, window = f"whfail:{ip}", 600
            if lim.count(fail_key, window) >= cfg.rate_webhook_fail_per_10min:
                return self.too_many(lim.retry_after(fail_key, window))
            ok, wait = lim.check(f"wh:{ip}", cfg.rate_webhook_per_min, 60)
            return None if ok else self.too_many(wait)
        if not path.startswith("/v1/") or path in ("/v1/health", "/v1/ready"):
            return None
        if path == "/v1/auth/google" and request.method == "POST":
            ok, wait = lim.check(f"login:{ip}", cfg.rate_login_per_5min, 300)
            if not ok:
                return self.too_many(wait)
        if is_sensitive(request):
            ok, wait = lim.check(
                "sens:" + _account_key(request, ip), cfg.rate_sensitive_per_10min, 600
            )
            if not ok:
                return self.too_many(wait)
        ok, wait = lim.check(f"api:{ip}", cfg.rate_api_per_min, 60)
        return None if ok else self.too_many(wait)

    def after(self, request: Request, status: int) -> None:
        """Webhook recusado (segredo, assinatura, conexão ou corpo errado): falha do IP."""
        if (
            self.cfg.rate_limit_enabled
            and request.url.path.startswith(WEBHOOKS)
            and status in FAILED_WEBHOOK
        ):
            self.limiter.add(f"whfail:{client_ip(request, self.cfg.trust_proxy)}")


class BodyTooLarge(Exception):
    """Corpo passou do limite no meio da leitura (sem Content-Length ou em pedaços)."""


def _is_too_large(exc: BaseException) -> bool:
    """O erro pode chegar embrulhado num grupo de exceções (as tarefas internas do Starlette)."""
    if isinstance(exc, BodyTooLarge):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_too_large(e) for e in exc.exceptions)
    return False


class BodyLimitMiddleware:
    """Recusa corpo grande demais antes de ele ir inteiro para a memória (inclusive em pedaços)."""

    def __init__(self, app: Callable[..., Awaitable[None]], max_bytes: int, cfg: Settings) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.cfg = cfg

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._refuse(send)
            return
        seen = 0
        started = False

        async def tracked(message: MutableMapping[str, Any]) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        async def limited() -> MutableMapping[str, Any]:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise BodyTooLarge
            return message

        try:
            await self.app(scope, limited, tracked)
        except BaseException as exc:
            if started or not _is_too_large(exc):
                raise
            await self._refuse(send)

    async def _refuse(self, send: Send) -> None:
        body = json.dumps(
            {"error": {"code": "payload_too_large", "message": "Corpo grande demais."}}
        ).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        headers += [(k.lower().encode(), v.encode()) for k, v in security_headers(self.cfg).items()]
        await send({"type": "http.response.start", "status": 413, "headers": headers})
        await send({"type": "http.response.body", "body": body})
