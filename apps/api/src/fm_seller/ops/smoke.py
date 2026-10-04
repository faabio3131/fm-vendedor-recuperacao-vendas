"""Teste de fumaça de um ambiente já no ar: `python -m fm_seller.cli smoke --api URL [--web URL]`.

Confere o que dá para provar de fora, sem login e sem credencial: a API está de pé e pronta
(banco acessível), não entrega dado sem login, não aceita escrita de outra origem, os webhooks
recusam quem não tem segredo, e o painel (se informado) abre e repassa `/v1` até a API.
Não prova login com Google, WhatsApp, Cakto nem IA: isso é com contas reais
(docs/LANCAMENTO_MVP.md).
Termina com 0 (tudo certo), 1 (só avisos) ou 2 (algo crítico). Não manda segredo nem cookie.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from fm_seller.ops.health import CRITICAL, WARNING, Finding
from fm_seller.ops.preflight import Report

EVIL = "https://origem-nao-permitida.example"
WAKE_STATUS = {502, 503, 504, 429}  # instância grátis do Render acordando


def _get(
    client: httpx.Client,
    url: str,
    *,
    wait: float,
    sleep: Callable[[float], None],
    headers: dict[str, str] | None = None,
    method: str = "GET",
) -> httpx.Response | None:
    """Pede a URL; se a instância estiver acordando (503...), tenta de novo até `wait` segundos."""
    waited = 0.0
    while True:
        try:
            res = client.request(method, url, headers=headers)
        except httpx.HTTPError:
            res = None
        if res is not None and res.status_code not in WAKE_STATUS:
            return res
        if waited >= wait:
            return res
        sleep(5)
        waited += 5


def _is_local(url: str) -> bool:
    return "localhost" in url or "testserver" in url or "127.0.0.1" in url


def missing_headers(res: httpx.Response, *, https: bool) -> list[str]:
    """Cabeçalhos de segurança que deveriam vir e não vieram (o proxy pode ter tirado)."""
    h = res.headers
    csp = h.get("content-security-policy", "")
    missing = []
    if h.get("x-content-type-options", "").lower() != "nosniff":
        missing.append("X-Content-Type-Options")
    if "frame-ancestors" not in csp and not h.get("x-frame-options"):
        missing.append("proteção contra embutir em outra página (frame-ancestors)")
    if not csp:
        missing.append("Content-Security-Policy")
    if not h.get("referrer-policy"):
        missing.append("Referrer-Policy")
    if https and not h.get("strict-transport-security"):
        missing.append("Strict-Transport-Security")
    return missing


def _json(res: httpx.Response) -> Any:
    try:
        return res.json()
    except ValueError:
        return None


def run(
    api: str,
    web: str | None = None,
    *,
    client: httpx.Client | None = None,
    wait: float = 90,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    report = Report()
    api = api.rstrip("/")
    own = client is None
    http = client or httpx.Client(timeout=20, follow_redirects=False)
    try:
        if api.startswith("http://") and "localhost" not in api and "testserver" not in api:
            report.bad(WARNING, "api_not_https", "A API não está em https.")

        res = _get(http, f"{api}/v1/health", wait=wait, sleep=sleep)
        body = None if res is None else _json(res)
        if res is not None and res.status_code == 200 and isinstance(body, dict):
            report.ok(f"/v1/health responde ok (versão {body.get('version', '?')})")
        else:
            code = "sem resposta" if res is None else str(res.status_code)
            report.bad(CRITICAL, "health", f"/v1/health não respondeu ok ({code}).")
            return report  # sem API de pé, o resto não faz sentido

        https_api = api.startswith("https://") and not _is_local(api)
        lacking = missing_headers(res, https=https_api)
        if lacking:
            report.bad(
                WARNING,
                "api_headers",
                "A API não enviou cabeçalhos de segurança: " + ", ".join(lacking) + ".",
            )
        else:
            report.ok("A API envia os cabeçalhos de segurança")
        if not _is_local(api):
            spec = _get(http, f"{api}/openapi.json", wait=0, sleep=sleep)
            if spec is not None and spec.status_code == 200:
                report.bad(
                    WARNING, "api_docs_open", "A descrição da API (/openapi.json) está exposta."
                )
            else:
                report.ok("A descrição da API não está exposta")

        res = _get(http, f"{api}/v1/ready", wait=wait, sleep=sleep)
        body = None if res is None else _json(res)
        if res is not None and res.status_code == 200 and (body or {}).get("status") == "ready":
            report.ok("/v1/ready: o banco responde")
        else:
            code = "sem resposta" if res is None else str(res.status_code)
            report.bad(CRITICAL, "ready", f"/v1/ready não está pronto ({code}): banco inacessível?")

        res = _get(http, f"{api}/v1/me", wait=0, sleep=sleep)
        if res is not None and res.status_code in (401, 403):
            report.ok("/v1/me exige login")
        else:
            code = "sem resposta" if res is None else str(res.status_code)
            report.bad(CRITICAL, "me_open", f"/v1/me sem login deveria dar 401, deu {code}.")

        res = _get(http, f"{api}/v1/health", wait=0, sleep=sleep, headers={"Origin": EVIL})
        allow = "" if res is None else res.headers.get("access-control-allow-origin", "")
        if allow in ("*", EVIL):
            report.bad(
                CRITICAL, "cors_open", "A API aceita chamadas do navegador de qualquer origem."
            )
        else:
            report.ok("CORS não libera origem estranha")

        res = _get(
            http,
            f"{api}/v1/auth/logout",
            wait=0,
            sleep=sleep,
            headers={"Origin": EVIL},
            method="POST",
        )
        if res is not None and res.status_code == 403:
            report.ok("Escrita vinda de outra origem é recusada")
        else:
            code = "sem resposta" if res is None else str(res.status_code)
            report.bad(
                CRITICAL, "origin_guard", f"Escrita de outra origem deveria dar 403, deu {code}."
            )

        res = _get(
            http,
            f"{api}/v1/webhooks/whatsapp_cloud/inexistente",
            wait=0,
            sleep=sleep,
        )
        res_hook = _get(
            http, f"{api}/v1/platform/webhooks/cakto", wait=0, sleep=sleep, method="POST"
        )
        wa_ok = res is not None and res.status_code in (403, 404)
        hook_ok = res_hook is not None and res_hook.status_code in (400, 401, 404, 422)
        if wa_ok and hook_ok:
            report.ok("Webhooks recusam quem não tem conexão ou segredo")
        else:
            report.bad(
                CRITICAL,
                "webhook_open",
                "Webhook sem conexão/segredo não foi recusado como esperado "
                f"(WhatsApp {None if res is None else res.status_code}, "
                f"plataforma {None if res_hook is None else res_hook.status_code}).",
            )

        if web:
            web = web.rstrip("/")
            page = _get(http, f"{web}/login", wait=wait, sleep=sleep)
            if page is not None and page.status_code == 200 and "AtendeVendeIA" in page.text:
                report.ok("O painel abre a tela de login")
                web_lacking = missing_headers(
                    page, https=web.startswith("https://") and not _is_local(web)
                )
                if web_lacking:
                    report.bad(
                        WARNING,
                        "web_headers",
                        "O painel não enviou cabeçalhos de segurança: "
                        + ", ".join(web_lacking)
                        + ".",
                    )
                else:
                    report.ok("O painel envia os cabeçalhos de segurança")
            else:
                code = "sem resposta" if page is None else str(page.status_code)
                report.bad(CRITICAL, "web_login", f"O painel não abriu o login ({code}).")
            proxy = _get(http, f"{web}/v1/health", wait=0, sleep=sleep)
            body = None if proxy is None else _json(proxy)
            if proxy is not None and proxy.status_code == 200 and isinstance(body, dict):
                report.ok("O painel repassa /v1 até a API (login vai funcionar com cookie)")
            else:
                code = "sem resposta" if proxy is None else str(proxy.status_code)
                report.bad(
                    CRITICAL,
                    "web_proxy",
                    f"O painel não repassa /v1 até a API ({code}): confira API_PROXY_TARGET.",
                )
            if web.startswith("http://") and "localhost" not in web:
                report.bad(WARNING, "web_not_https", "O painel não está em https.")
    finally:
        if own:
            http.close()
    return report


def findings(report: Report) -> list[Finding]:
    return report.findings
