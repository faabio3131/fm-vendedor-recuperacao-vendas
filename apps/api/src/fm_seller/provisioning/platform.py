"""Compras do próprio SaaS (vendidas na Cakto/Hotmart da F&M) viram conta de cliente.

Compra aprovada → cliente + plano + convite para o e-mail do comprador, que entra com a conta
Google desse e-mail. Atraso/cancelamento/reembolso mudam o status do plano (cancelado não libera
nada). Tudo é idempotente: o mesmo evento nunca cria dois clientes.

ATENÇÃO — formato a confirmar com evento real (ver events/normalize.py). O vínculo
produto → plano é DADO (`plan_products`), cadastrado com `map-product`.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from fm_seller.config import Settings
from fm_seller.db import Conn, Database
from fm_seller.errors import AppError, bad_request, not_found
from fm_seller.events import normalize as n
from fm_seller.events.ingest import MAX_BODY, dedupe_key, secret_ok
from fm_seller.events.normalize import NORMALIZERS, CheckoutEvent
from fm_seller.security.crypto import SecretBox
from fm_seller.services import audit

log = logging.getLogger("fm_seller.platform")
INVITE_DAYS = 30
RETRY_OUTCOMES = ("unmapped_product", "failed")

ACTIVATING = (n.PURCHASE_APPROVED, n.SUBSCRIPTION_ACTIVE)
STATUS_BY_KIND = {
    n.SUBSCRIPTION_LATE: "past_due",
    n.SUBSCRIPTION_RECOVERED: "active",
    n.SUBSCRIPTION_CANCELED: "canceled",
    n.REFUNDED: "canceled",
}


def _expected_secret(settings: Settings, provider: str) -> str:
    return {
        "cakto": settings.platform_cakto_secret,
        "hotmart": settings.platform_hotmart_hottok,
    }.get(provider, "")


def _find_tenant(conn: Conn, email: str) -> uuid.UUID | None:
    """Cliente cujo dono (ou convidado como dono) tem esse e-mail."""
    row = conn.execute(
        "SELECT tenant_id FROM ("
        " SELECT m.tenant_id, m.created_at FROM memberships m JOIN users u ON u.id = m.user_id "
        "  WHERE lower(u.email) = lower(%s) AND m.role = 'owner' "
        " UNION ALL"
        " SELECT tenant_id, created_at FROM pending_invites "
        "  WHERE lower(email) = lower(%s) AND role = 'owner'"
        ") x ORDER BY created_at LIMIT 1",
        (email, email),
    ).fetchone()
    return None if row is None else row["tenant_id"]


def _apply(conn: Conn, provider: str, ev: CheckoutEvent) -> tuple[str, uuid.UUID | None]:
    """Aplica o efeito do evento. Devolve (resultado, cliente)."""
    if not ev.email:
        return "no_email", None
    # Serializa eventos do mesmo comprador (ex.: compra + assinatura chegando juntas).
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (ev.email,))
    tenant_id = _find_tenant(conn, ev.email)

    if ev.kind in ACTIVATING:
        plan = conn.execute(
            "SELECT plan_key FROM plan_products WHERE provider = %s AND external_product_id = %s",
            (provider, ev.product_id),
        ).fetchone()
        if plan is None:
            return "unmapped_product", None
        if tenant_id is None:
            created = conn.execute(
                "INSERT INTO tenants (name) VALUES (%s) RETURNING id",
                ((ev.name or ev.email)[:200],),
            ).fetchone()
            assert created is not None
            tenant_id = created["id"]
            conn.execute(
                "INSERT INTO tenant_plans (tenant_id, plan_key, source, external_ref) "
                "VALUES (%s, %s, %s, %s)",
                (tenant_id, plan["plan_key"], provider, ev.external_ref),
            )
            conn.execute(
                "INSERT INTO pending_invites (tenant_id, email, role, expires_at) "
                "VALUES (%s, lower(%s), 'owner', %s)",
                (tenant_id, ev.email, datetime.now(UTC) + timedelta(days=INVITE_DAYS)),
            )
            audit(conn, tenant_id=tenant_id, actor=None, action="tenant.created", target=ev.email)
            return "tenant_created", tenant_id
        conn.execute(
            "UPDATE tenant_plans SET plan_key = %s, status = 'active', source = %s, "
            "external_ref = %s WHERE tenant_id = %s",
            (plan["plan_key"], provider, ev.external_ref, tenant_id),
        )
        audit(
            conn, tenant_id=tenant_id, actor=None, action="plan.activated", target=plan["plan_key"]
        )
        return "plan_updated", tenant_id

    status = STATUS_BY_KIND.get(ev.kind)
    if status is None:
        return "ignored", None
    if tenant_id is None:
        return "tenant_not_found", None
    conn.execute("UPDATE tenant_plans SET status = %s WHERE tenant_id = %s", (status, tenant_id))
    audit(conn, tenant_id=tenant_id, actor=None, action=f"plan.{status}", target=ev.email)
    return "plan_status_changed", tenant_id


def _run(db: Database, event_id: uuid.UUID, provider: str, ev: CheckoutEvent) -> str:
    try:
        with db.tx(system=True) as conn:
            outcome, tenant_id = _apply(conn, provider, ev)
            conn.execute(
                "UPDATE platform_events SET outcome = %s, tenant_id = %s, error = NULL, "
                "processed_at = now() WHERE id = %s",
                (outcome, tenant_id, event_id),
            )
        return outcome
    except Exception as exc:
        log.exception("falha na compra da plataforma", extra={"ctx": {"event_id": str(event_id)}})
        with db.tx(system=True) as conn:
            conn.execute(
                "UPDATE platform_events SET outcome = 'failed', error = %s, processed_at = now() "
                "WHERE id = %s",
                (type(exc).__name__ + ": " + str(exc)[:300], event_id),
            )
        return "failed"


def ingest_platform_event(
    db: Database,
    box: SecretBox,
    settings: Settings,
    *,
    provider: str,
    headers: Mapping[str, str],
    raw_body: bytes,
) -> str:
    if provider not in NORMALIZERS:
        raise not_found("Provedor sem recebimento de compras.")
    expected = _expected_secret(settings, provider)
    if not expected:
        raise not_found("Recebimento de compras não está configurado.")
    if len(raw_body) > MAX_BODY:
        raise AppError(413, "payload_too_large", "Corpo grande demais.")
    try:
        body: Any = json.loads(raw_body)
    except ValueError as exc:
        raise bad_request("invalid_json", "Corpo não é JSON válido.") from exc
    if not isinstance(body, dict):
        raise bad_request("invalid_json", "Corpo precisa ser um objeto JSON.")
    if not secret_ok(provider, headers, body, expected, raw_body):
        raise AppError(401, "invalid_secret", "Segredo do webhook inválido.")

    ev = NORMALIZERS[provider](body)
    event_type = str(body.get("event", ""))[:100] or "unknown"
    with db.tx(system=True) as conn:
        row = conn.execute(
            "INSERT INTO platform_events (provider, dedupe_key, event_type, buyer_email, "
            "payload_encrypted, outcome) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (provider, dedupe_key) DO NOTHING RETURNING id",
            (
                provider,
                dedupe_key(body, raw_body, provider),
                event_type,
                ev.email if ev else None,
                box.encrypt(body, tenant_id="platform", provider=f"platform:{provider}"),
                "ignored" if ev is None else "received",
            ),
        ).fetchone()
    if row is None:
        return "duplicate"
    if ev is None:
        return "ignored"
    return _run(db, row["id"], provider, ev)


def reprocess_platform(db: Database, box: SecretBox, *, limit: int = 100) -> int:
    """Refaz compras que ficaram sem efeito (produto ainda sem plano, falha). Devolve quantas."""
    with db.tx(system=True) as conn:
        rows = conn.execute(
            "SELECT id, provider, payload_encrypted FROM platform_events "
            "WHERE outcome = ANY(%s) ORDER BY received_at LIMIT %s",
            (list(RETRY_OUTCOMES), limit),
        ).fetchall()
    done = 0
    for r in rows:
        body = box.decrypt(
            r["payload_encrypted"], tenant_id="platform", provider=f"platform:{r['provider']}"
        )
        ev = NORMALIZERS[r["provider"]](body)
        if ev is None:
            continue
        done += _run(db, r["id"], r["provider"], ev) not in RETRY_OUTCOMES
    return done
