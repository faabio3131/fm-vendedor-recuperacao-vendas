"""Reconciliação periódica das licenças pela API do Command (somente leitura).

Faz duas coisas que o webhook sozinho não garante:
1. **Corrige perda e atraso:** aplica a versão mais nova de cada licença (regra dos eventos).
2. **Recupera o provisionamento:** licença autorizada que ainda não tem conta (webhook perdido,
   plano ainda sem mapeamento) é provisionada aqui, com a mesma regra anti-duplicidade.
A API do Command ainda NÃO existe (a PR #62 do FM-CONTROL-CENTER não a implementa); por isso
tudo aqui só roda com `FM_FMCOMMAND_API_BASE_URL` e token configurados, e o modo `off` nunca
chama a rede.
Nenhum serviço escreve no banco do outro: aqui só há `GET`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.licensing import service
from fm_seller.licensing.contract import SCHEMA_VERSION, ContractError, LicenseItem, parse_item
from fm_seller.security.crypto import SecretBox
from fm_seller.services import audit

log = logging.getLogger("fm_seller.licensing")
PAGE_LIMIT = 100
MAX_PAGES = 200


class ReconcileError(Exception):
    """Falha da reconciliação. O texto é só o tipo: nunca ecoa corpo, token nem endereço."""


class LicenseApiClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        product_code: str,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ReconcileError("base_url_invalida")
        if len(token) < 32:
            raise ReconcileError("token_invalido")
        self._base = base_url.rstrip("/")
        self._product = product_code
        self._http = httpx.Client(
            timeout=timeout,
            follow_redirects=False,  # nunca segue redirecionamento com o token na mão
            transport=transport,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

    def close(self) -> None:
        self._http.close()

    def page(self, cursor: str | None) -> tuple[list[LicenseItem], str | None]:
        params = {"product_code": self._product, "limit": str(PAGE_LIMIT)}
        if cursor:
            params["cursor"] = cursor
        try:
            res = self._http.get(f"{self._base}/v1/licenses", params=params)
        except httpx.HTTPError as exc:
            raise ReconcileError("rede") from exc
        if res.status_code in (401, 403):
            raise ReconcileError("credencial_recusada")
        if res.status_code == 429 or res.status_code >= 500:
            raise ReconcileError("command_indisponivel")
        if res.status_code != 200:
            raise ReconcileError(f"resposta_{res.status_code}")
        try:
            body: Any = res.json()
        except ValueError as exc:
            raise ReconcileError("resposta_invalida") from exc
        if (
            not isinstance(body, dict)
            or body.get("schema_version") != SCHEMA_VERSION
            or not isinstance(body.get("items"), list)
        ):
            raise ReconcileError("resposta_invalida")
        try:
            items = [parse_item(i) for i in body["items"]]
        except ContractError as exc:
            raise ReconcileError("item_fora_do_contrato") from exc
        nxt = body.get("next_cursor")
        if nxt is not None and not isinstance(nxt, str):
            raise ReconcileError("resposta_invalida")
        return items, nxt


@dataclass(frozen=True)
class ReconcileResult:
    ok: bool
    licenses: int = 0
    applied: int = 0
    provisioned: int = 0
    pending_adoption: int = 0
    failed: int = 0
    error: str | None = None


_ACTIONABLE = {"applied", "applied_plan_unmapped"}


def reconcile(
    db: Database,
    box: SecretBox,
    settings: Settings,
    client: LicenseApiClient,
    *,
    now: datetime | None = None,
) -> ReconcileResult:
    """Uma passada completa pela API. Falha de rede ou de contrato NÃO altera nenhuma licença."""
    stamp = now or datetime.now(UTC)
    with db.tx(system=True) as conn:
        conn.execute("UPDATE license_sync_state SET last_attempt_at = %s", (stamp,))
    items: list[LicenseItem] = []
    try:
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            page, cursor = client.page(cursor)
            items.extend(page)
            if cursor is None:
                break
        else:
            raise ReconcileError("paginas_demais")
    except ReconcileError as exc:
        _record_failure(db, str(exc))
        return ReconcileResult(False, error=str(exc))
    for item in items:
        if item.license.product_code != settings.fmcommand_product_code:
            _record_failure(db, "produto_diferente")
            return ReconcileResult(False, error="produto_diferente")

    applied = provisioned = adoption = failed = 0
    seen: list[Any] = []
    for item in items:
        lic = item.license
        seen.append(lic.license_id)
        try:
            with db.tx(system=True) as conn:
                res = service._dispatch(
                    settings,
                    conn,
                    lic,
                    item.provisioning,
                    item.source,
                    channel="reconciliation",
                    now=stamp,
                )
                if res.outcome in _ACTIONABLE or res.outcome in (
                    "provisioned",
                    "requires_adoption",
                    "unmapped_plan",
                    "unauthorized_provisioning",
                ):
                    conn.execute(
                        "INSERT INTO license_events (event_id, event_type, channel, mode, "
                        "license_id, license_version, tenant_id, payload_encrypted, outcome, "
                        "processed_at) VALUES (%s, 'reconciliation', 'reconciliation', %s, %s, "
                        "%s, %s, %s, %s, now()) ON CONFLICT (event_id) DO UPDATE SET "
                        "outcome = EXCLUDED.outcome, tenant_id = EXCLUDED.tenant_id, "
                        "processed_at = now()",
                        (
                            f"recon:{lic.license_id}:{lic.version}",
                            settings.fmcommand_mode,
                            lic.license_id,
                            lic.version,
                            res.tenant_id,
                            box.encrypt(
                                {"license_id": str(lic.license_id), "version": lic.version},
                                tenant_id="platform",
                                provider="license:fmcommand",
                            ),
                            res.outcome,
                        ),
                    )
            applied += res.outcome in _ACTIONABLE
            provisioned += res.outcome == "provisioned"
            adoption += res.outcome == "requires_adoption"
        except Exception:
            failed += 1
            log.exception(
                "falha ao reconciliar licença", extra={"ctx": {"license": str(lic.license_id)}}
            )
    if settings.fmcommand_mode == "enforce":
        _flag_missing(db, seen, stamp)
    with db.tx(system=True) as conn:
        conn.execute(
            "UPDATE license_sync_state SET last_success_at = %s, last_error = NULL, "
            "consecutive_failures = 0",
            (stamp,),
        )
    return ReconcileResult(True, len(items), applied, provisioned, adoption, failed)


def _flag_missing(db: Database, seen: list[Any], now: datetime) -> None:
    """Vínculo do Command que a API não devolveu: só registra (uma vez por dia), não muda estado."""
    with db.tx(system=True) as conn:
        rows = conn.execute(
            "SELECT tenant_id, license_id FROM commercial_links WHERE authority = 'fmcommand' "
            "AND NOT (license_id = ANY(%s::uuid[]))",
            (seen,),
        ).fetchall()
        for r in rows:
            recent = conn.execute(
                "SELECT 1 FROM audit_log WHERE tenant_id = %s AND action = %s AND at > %s",
                (r["tenant_id"], "license.missing_in_command", now - timedelta(days=1)),
            ).fetchone()
            if recent is None:
                audit(
                    conn,
                    tenant_id=r["tenant_id"],
                    actor=None,
                    action="license.missing_in_command",
                    target=str(r["license_id"]),
                )


def _record_failure(db: Database, reason: str) -> None:
    with db.tx(system=True) as conn:
        conn.execute(
            "UPDATE license_sync_state SET last_error = %s, "
            "consecutive_failures = consecutive_failures + 1",
            (reason[:60],),
        )


def due(db: Database, settings: Settings, now: datetime) -> bool:
    with db.tx(system=True) as conn:
        row = conn.execute("SELECT last_attempt_at FROM license_sync_state").fetchone()
    last = None if row is None else row["last_attempt_at"]
    return last is None or now - last >= timedelta(minutes=settings.fmcommand_reconcile_minutes)


def build_client(settings: Settings) -> LicenseApiClient | None:
    """Cliente da API de licenças, ou None se o modo é `off` ou a API não está configurada."""
    token = settings.fmcommand_api_token.get_secret_value()
    if settings.fmcommand_mode == "off" or not settings.fmcommand_api_base_url or not token:
        return None
    return LicenseApiClient(
        settings.fmcommand_api_base_url,
        token,
        settings.fmcommand_product_code,
        timeout=settings.fmcommand_api_timeout_seconds,
    )


def cycle(
    db: Database,
    box: SecretBox,
    settings: Settings,
    *,
    now: datetime | None = None,
    client_factory: Callable[[Settings], LicenseApiClient | None] = build_client,
) -> dict[str, Any]:
    """Passo do worker: reconcilia se for a hora e encerra janelas de contingência vencidas."""
    from fm_seller.licensing.contingency import enforce_licenses

    stamp = now or datetime.now(UTC)
    out: dict[str, Any] = {}
    if settings.fmcommand_mode == "off":
        return out
    if due(db, settings, stamp):
        client = client_factory(settings)
        if client is not None:
            try:
                r = reconcile(db, box, settings, client, now=stamp)
                out["reconciliacao"] = r.__dict__
            finally:
                client.close()
    out["contingencia"] = enforce_licenses(db, settings, now=stamp)
    return out
