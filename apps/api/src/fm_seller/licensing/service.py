"""Aplicação das licenças do Command: provisiona, aplica estado, registra o modo sombra.

Regras (ADR-0005), todas testadas:
- **Autoridade única por assinatura.** Só aplica quando `commercial_links.authority = 'fmcommand'`.
  Evento de assinatura que ainda é `direct` (Cakto/Hotmart) é registrado e NÃO aplicado.
- **Sem duplicidade de conta.** Antes de criar tenant, procura conta existente pelo e-mail do
  dono e pela referência externa da assinatura; achou, responde `requires_adoption` (a virada exige
  aprovação humana registrada, ver `authority.py`).
- **Ordem não importa.** A licença carrega o estado completo e a versão; versão menor ou igual é
  `stale`. Transição acontece pelo estado, nunca pelo tipo do evento.
- **Idempotência** por `event_id` (`license_events`).
- **Modo sombra nunca altera licença real**: só escreve em `license_shadow` e `license_events`.
Nada aqui escreve em Cakto/Hotmart, `platform_events` nem no checkout.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fm_seller.config import Settings, parse_webhook_secrets
from fm_seller.db import Conn, Database
from fm_seller.errors import AppError, bad_request, not_found
from fm_seller.licensing import signature
from fm_seller.licensing.contract import (
    STATUS_FOR_STATE,
    ContractError,
    License,
    LicenseEvent,
    Provisioning,
    Source,
    parse_event,
)
from fm_seller.provisioning import lifecycle
from fm_seller.security.crypto import SecretBox
from fm_seller.services import audit

log = logging.getLogger("fm_seller.licensing")
MAX_BODY = 256 * 1024
INVITE_DAYS = 30
DIRECT_GATEWAYS = ("cakto", "hotmart")


@dataclass(frozen=True)
class Applied:
    outcome: str
    tenant_id: uuid.UUID | None = None


def _direct_ref(source: Source | None) -> bool:
    """A origem aponta para uma assinatura direta (Cakto/Hotmart) com referência externa?"""
    return bool(source and source.gateway in DIRECT_GATEWAYS and source.external_subscription_ref)


def _lock(conn: Conn, license_id: uuid.UUID) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"license:{license_id}",))


def _existing_direct_tenant(
    conn: Conn, prov: Provisioning, source: Source | None
) -> uuid.UUID | None:
    """Conta que já existe para este cliente (dono ou convite), pelo e-mail ou pela referência."""
    if prov.admin_email:
        row = conn.execute(
            "SELECT tenant_id FROM ("
            " SELECT m.tenant_id, m.created_at FROM memberships m JOIN users u ON u.id = m.user_id "
            "  WHERE lower(u.email) = lower(%s) AND m.role = 'owner' "
            " UNION ALL"
            " SELECT tenant_id, created_at FROM pending_invites "
            "  WHERE lower(email) = lower(%s) AND role = 'owner'"
            ") x ORDER BY created_at LIMIT 1",
            (prov.admin_email, prov.admin_email),
        ).fetchone()
        if row is not None:
            return uuid.UUID(str(row["tenant_id"]))
    if (
        source is not None
        and source.gateway in DIRECT_GATEWAYS
        and source.external_subscription_ref
    ):
        row = conn.execute(
            "SELECT tenant_id FROM tenant_plans WHERE source = %s AND external_ref = %s",
            (source.gateway, source.external_subscription_ref),
        ).fetchone()
        if row is not None:
            return uuid.UUID(str(row["tenant_id"]))
    return None


def _plan_for(conn: Conn, plan_code: str) -> str | None:
    row = conn.execute(
        "SELECT plan_key FROM plan_products WHERE provider = 'fmcommand' "
        "AND external_product_id = %s",
        (plan_code,),
    ).fetchone()
    return None if row is None else str(row["plan_key"])


def _insert_link(
    conn: Conn, tenant_id: uuid.UUID, lic: License, *, authority: str, now: datetime
) -> None:
    conn.execute(
        "INSERT INTO commercial_links (tenant_id, product_code, customer_id, subscription_id, "
        "license_id, authority, license_version, state, plan_code, valid_from, valid_until, "
        "grace_ends_at, last_validated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            tenant_id,
            lic.product_code,
            lic.customer_id,
            lic.subscription_id,
            lic.license_id,
            authority,
            lic.version,
            lic.state,
            lic.plan_code,
            lic.valid_from,
            lic.valid_until,
            lic.grace_ends_at,
            now,
        ),
    )


def _provision(
    conn: Conn,
    lic: License,
    prov: Provisioning | None,
    source: Source | None,
    now: datetime,
) -> Applied:
    if lic.state not in ("active", "past_due"):
        return Applied("no_link_inactive")
    if prov is None or not prov.authorized:
        return Applied("unauthorized_provisioning")
    existing = _existing_direct_tenant(conn, prov, source)
    if existing is not None:
        return Applied("requires_adoption", existing)
    plan_key = _plan_for(conn, lic.plan_code)
    if plan_key is None:
        return Applied("unmapped_plan")
    name = prov.account_name or prov.admin_name or prov.admin_email
    created = conn.execute(
        "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (name[:200],)
    ).fetchone()
    assert created is not None
    tenant_id: uuid.UUID = created["id"]
    conn.execute(
        "INSERT INTO tenant_plans (tenant_id, plan_key, source, external_ref) "
        "VALUES (%s, %s, 'fmcommand', %s)",
        (tenant_id, plan_key, str(lic.license_id)),
    )
    conn.execute(
        "INSERT INTO pending_invites (tenant_id, email, role, expires_at) "
        "VALUES (%s, lower(%s), 'owner', %s)",
        (tenant_id, prov.admin_email, now + timedelta(days=INVITE_DAYS)),
    )
    _insert_link(conn, tenant_id, lic, authority="fmcommand", now=now)
    if lic.state == "past_due":
        lifecycle.set_status(
            conn, tenant_id, lifecycle.PAST_DUE, target=str(lic.license_id), now=now
        )
    audit(
        conn,
        tenant_id=tenant_id,
        actor=None,
        action="tenant.created",
        target=str(lic.license_id),
        detail={"origin": "fmcommand", "license_version": lic.version},
    )
    return Applied("provisioned", tenant_id)


def _apply_to_link(
    conn: Conn,
    link: dict[str, Any],
    lic: License,
    *,
    channel: str,
    now: datetime,
) -> Applied:
    tenant_id: uuid.UUID = link["tenant_id"]
    if link["authority"] != "fmcommand":
        return Applied("not_authoritative", tenant_id)
    if lic.version < link["license_version"] or (
        lic.version == link["license_version"] and channel == "webhook"
    ):
        return Applied("stale", tenant_id)
    if lic.version == link["license_version"]:
        # Reconciliação reconfirmou a mesma versão: só renova a validação. NÃO reabre contingência
        # nem prorroga datas (só licença nova do Command muda datas).
        conn.execute(
            "UPDATE commercial_links SET last_validated_at = %s, updated_at = %s "
            "WHERE tenant_id = %s",
            (now, now, tenant_id),
        )
        return Applied("validated", tenant_id)
    new_status = STATUS_FOR_STATE.get(lic.state)
    if new_status is None:  # `pending` depois de já existir licença: não vira estado
        return Applied("ignored", tenant_id)
    current = conn.execute(
        "SELECT status, plan_key FROM tenant_plans WHERE tenant_id = %s FOR UPDATE", (tenant_id,)
    ).fetchone()
    outcome = "applied"
    if current is not None:
        if current["status"] != new_status:
            lifecycle.set_status(conn, tenant_id, new_status, target=str(lic.license_id), now=now)
        if lic.plan_code != link["plan_code"]:
            plan_key = _plan_for(conn, lic.plan_code)
            if plan_key is None:
                outcome = "applied_plan_unmapped"  # o estado vale; o plano espera o mapeamento
            elif plan_key != current["plan_key"]:
                conn.execute(
                    "UPDATE tenant_plans SET plan_key = %s WHERE tenant_id = %s",
                    (plan_key, tenant_id),
                )
    plan_code = link["plan_code"] if outcome == "applied_plan_unmapped" else lic.plan_code
    conn.execute(
        "UPDATE commercial_links SET license_version = %s, state = %s, plan_code = %s, "
        "valid_from = %s, valid_until = %s, grace_ends_at = %s, last_validated_at = %s, "
        "contingency_version = NULL, contingency_started_at = NULL, contingency_until = NULL, "
        "contingency_exhausted_at = NULL, updated_at = %s WHERE tenant_id = %s",
        (
            lic.version,
            lic.state,
            plan_code,
            lic.valid_from,
            lic.valid_until,
            lic.grace_ends_at,
            now,
            now,
            tenant_id,
        ),
    )
    audit(
        conn,
        tenant_id=tenant_id,
        actor=None,
        action=f"license.{lic.state}",
        target=str(lic.license_id),
        detail={"version": lic.version, "channel": channel},
    )
    return Applied(outcome, tenant_id)


def apply_license(
    conn: Conn,
    lic: License,
    prov: Provisioning | None,
    source: Source | None,
    *,
    channel: str,
    now: datetime,
) -> Applied:
    """Aplica uma licença (de evento ou da reconciliação). Chamar dentro de `tx(system=True)`."""
    _lock(conn, lic.license_id)
    link = conn.execute(
        "SELECT * FROM commercial_links WHERE license_id = %s FOR UPDATE", (lic.license_id,)
    ).fetchone()
    if link is not None:
        if (
            link["customer_id"] != lic.customer_id
            or link["subscription_id"] != lic.subscription_id
            or link["product_code"] != lic.product_code
        ):
            audit(
                conn,
                tenant_id=link["tenant_id"],
                actor=None,
                action="license.identity_mismatch",
                target=str(lic.license_id),
            )
            return Applied("identity_mismatch", link["tenant_id"])
        return _apply_to_link(conn, link, lic, channel=channel, now=now)
    other = conn.execute(
        "SELECT tenant_id FROM commercial_links WHERE subscription_id = %s", (lic.subscription_id,)
    ).fetchone()
    if other is not None:
        return Applied("identity_mismatch", other["tenant_id"])
    if lic.state == "pending":
        return Applied("pending")
    return _provision(conn, lic, prov, source, now)


def record_shadow(conn: Conn, lic: License, source: Source | None, *, now: datetime) -> Applied:
    """Modo sombra: registra o que o Command diz e compara com o real. NUNCA altera licença real."""
    tenant_id: uuid.UUID | None = None
    link = conn.execute(
        "SELECT tenant_id FROM commercial_links WHERE license_id = %s", (lic.license_id,)
    ).fetchone()
    if link is not None:
        tenant_id = link["tenant_id"]
    elif (
        source is not None
        and source.gateway in DIRECT_GATEWAYS
        and source.external_subscription_ref
    ):
        row = conn.execute(
            "SELECT tenant_id FROM tenant_plans WHERE source = %s AND external_ref = %s",
            (source.gateway, source.external_subscription_ref),
        ).fetchone()
        tenant_id = None if row is None else row["tenant_id"]
    real: str | None = None
    if tenant_id is not None:
        plan = conn.execute(
            "SELECT status FROM tenant_plans WHERE tenant_id = %s", (tenant_id,)
        ).fetchone()
        real = None if plan is None else str(plan["status"])
    expected = STATUS_FOR_STATE.get(lic.state)
    diverges = real is not None and expected is not None and real != expected
    before = conn.execute(
        "SELECT license_version, diverges FROM license_shadow WHERE license_id = %s",
        (lic.license_id,),
    ).fetchone()
    conn.execute(
        "INSERT INTO license_shadow (license_id, customer_id, subscription_id, product_code, "
        "license_version, state, plan_code, valid_until, grace_ends_at, matched_tenant_id, "
        "real_status, expected_status, diverges, observed_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (license_id) DO UPDATE SET customer_id = EXCLUDED.customer_id, "
        "subscription_id = EXCLUDED.subscription_id, product_code = EXCLUDED.product_code, "
        "license_version = EXCLUDED.license_version, state = EXCLUDED.state, "
        "plan_code = EXCLUDED.plan_code, valid_until = EXCLUDED.valid_until, "
        "grace_ends_at = EXCLUDED.grace_ends_at, matched_tenant_id = EXCLUDED.matched_tenant_id, "
        "real_status = EXCLUDED.real_status, expected_status = EXCLUDED.expected_status, "
        "diverges = EXCLUDED.diverges, observed_at = EXCLUDED.observed_at "
        "WHERE license_shadow.license_version <= EXCLUDED.license_version",
        (
            lic.license_id,
            lic.customer_id,
            lic.subscription_id,
            lic.product_code,
            lic.version,
            lic.state,
            lic.plan_code,
            lic.valid_until,
            lic.grace_ends_at,
            tenant_id,
            real,
            expected,
            diverges,
            now,
        ),
    )
    newly = diverges and (before is None or not before["diverges"])
    if newly and tenant_id is not None:
        audit(
            conn,
            tenant_id=tenant_id,
            actor=None,
            action="license.shadow_divergence",
            target=str(lic.license_id),
            detail={"real": real, "expected": expected, "version": lic.version},
        )
    return Applied("shadow", tenant_id)


def _dispatch(
    settings: Settings,
    conn: Conn,
    lic: License,
    prov: Provisioning | None,
    source: Source | None,
    *,
    channel: str,
    now: datetime,
) -> Applied:
    if settings.fmcommand_mode == "shadow":
        return record_shadow(conn, lic, source, now=now)
    return apply_license(conn, lic, prov, source, channel=channel, now=now)


def ingest_webhook(
    db: Database,
    box: SecretBox,
    settings: Settings,
    *,
    headers: dict[str, str],
    raw_body: bytes,
    now: datetime | None = None,
) -> str:
    """Recebe um evento `fmcc.license.v1`. Devolve o resultado (o corpo da resposta 200)."""
    secrets = parse_webhook_secrets(settings.fmcommand_webhook_secrets)
    if settings.fmcommand_mode == "off" or not secrets or not settings.fmcommand_product_code:
        raise not_found("Recebimento de licenças não está configurado.")
    if len(raw_body) > MAX_BODY:
        raise AppError(413, "payload_too_large", "Corpo grande demais.")
    h = {k.lower(): v for k, v in headers.items()}
    stamp = now or datetime.now(UTC)
    try:
        signature.verify(h.get("x-fmcc-signature"), raw_body, secrets, now=stamp.timestamp())
    except signature.SignatureError as exc:
        raise AppError(401, "invalid_signature", "Assinatura inválida.") from exc
    try:
        body: Any = json.loads(raw_body)
    except ValueError as exc:
        raise bad_request("invalid_json", "Corpo não é JSON válido.") from exc
    if not isinstance(body, dict):
        raise bad_request("invalid_json", "Corpo precisa ser um objeto JSON.")
    try:
        event = parse_event(body)
    except ContractError as exc:
        raise AppError(exc.status, exc.code, "Evento fora do contrato.") from exc
    if h.get("x-fmcc-event-id") != event.event_id:
        raise AppError(401, "event_id_mismatch", "Identificador do evento não confere.")
    if event.license.product_code != settings.fmcommand_product_code:
        raise AppError(422, "unsupported_product", "Produto desconhecido.")
    return _record_and_process(db, box, settings, event, body, stamp)


def _record_and_process(
    db: Database,
    box: SecretBox,
    settings: Settings,
    event: LicenseEvent,
    body: dict[str, Any],
    now: datetime,
) -> str:
    mode = settings.fmcommand_mode
    with db.tx(system=True) as conn:
        row = conn.execute(
            "INSERT INTO license_events (event_id, event_type, channel, mode, license_id, "
            "license_version, payload_encrypted) VALUES (%s, %s, 'webhook', %s, %s, %s, %s) "
            "ON CONFLICT (event_id) DO NOTHING RETURNING id",
            (
                event.event_id,
                event.type,
                mode,
                event.license.license_id,
                event.license.version,
                box.encrypt(body, tenant_id="platform", provider="license:fmcommand"),
            ),
        ).fetchone()
    if row is None:
        return "duplicate"
    try:
        with db.tx(system=True) as conn:
            result = _dispatch(
                settings,
                conn,
                event.license,
                event.provisioning,
                event.source,
                channel="webhook",
                now=now,
            )
            conn.execute(
                "UPDATE license_events SET outcome = %s, tenant_id = %s, processed_at = now() "
                "WHERE id = %s",
                (result.outcome, result.tenant_id, row["id"]),
            )
        return result.outcome
    except Exception as exc:
        log.exception("falha ao aplicar licença", extra={"ctx": {"event_id": event.event_id}})
        with db.tx(system=True) as conn:
            conn.execute(
                "UPDATE license_events SET outcome = 'failed', error = %s, processed_at = now() "
                "WHERE id = %s",
                (type(exc).__name__, row["id"]),
            )
        return "failed"
