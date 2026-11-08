"""Open a neutral follow-up only after a *confirmed* seller checkout-link delivery.

A WhatsApp checkout URL alone is NOT a payment-status signal. This uses the
existing conversation_cold sequence, so no unpaid/PIX claims are fabricated.
The payment platform's purchase webhook remains authoritative for closing.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from fm_seller.db import Conn, Database
from fm_seller.events import normalize as n
from fm_seller.recovery.engine import Opportunity, load_settings, open_opportunity
from fm_seller.recovery.timing import next_allowed

FIRST_FOLLOWUP = timedelta(minutes=45)
MAX_LOOKBACK = timedelta(hours=24)
_URL = re.compile(r"https://[^\s<>]+")
_CTA = re.compile(r"\b(?:link seguro|link de pagamento|para contratar|para finalizar)\b", re.I)


def record_confirmed_checkout_link(
    conn: Conn, tenant_id: uuid.UUID, message_id: uuid.UUID, *, now: datetime | None = None
) -> bool:
    """Idempotently record checkout interest; only trusted provider-confirmed bot output."""
    now = now or datetime.now(UTC)
    row = conn.execute(
        "SELECT m.id, m.body, m.created_at, m.status, m.provider_message_id, "
        "m.author, m.direction, cv.last_inbound_at, ct.name, ct.phone "
        "FROM messages m JOIN conversations cv ON cv.id = m.conversation_id "
        "AND cv.tenant_id = m.tenant_id "
        "JOIN contacts ct ON ct.id = cv.contact_id AND ct.tenant_id = cv.tenant_id "
        "WHERE m.id = %s AND m.tenant_id = %s AND cv.channel = 'whatsapp'",
        (message_id, tenant_id),
    ).fetchone()
    if row is None:
        return False
    created = row["created_at"]
    inbound = row["last_inbound_at"]
    if (
        row["author"] != "bot"
        or row["direction"] != "out"
        or row["status"] not in ("sent", "delivered", "read")
        or not row["provider_message_id"]
        or not row["phone"]
        or inbound is None
        or not (now - MAX_LOOKBACK <= created <= now)
        or not (created - MAX_LOOKBACK <= inbound <= created)
    ):
        return False
    body = str(row["body"] or "")
    if not _CTA.search(body):
        return False
    urls = {m.group(0).rstrip(".,);") for m in _URL.finditer(body)}
    if not urls:
        return False
    offers = conn.execute(
        "SELECT name, price_cents, payment_url FROM offers "
        "WHERE tenant_id = %s AND active AND payment_url LIKE 'https://%%'",
        (tenant_id,),
    ).fetchall()
    matched = [o for o in offers if o["payment_url"] in urls]
    if len(matched) != 1:
        return False
    offer = matched[0]
    if not isinstance(offer["price_cents"], int) or offer["price_cents"] <= 0:
        return False
    case_id, _ = open_opportunity(
        conn,
        tenant_id,
        Opportunity(
            kind=n.CONVERSATION_COLD,
            source="conversa",
            external_ref=f"offer-link:{message_id}",
            name=str(row["name"] or ""),
            phone=str(row["phone"]),
            product_name=str(offer["name"]),
            amount_cents=int(offer["price_cents"]),
            payment_url=str(offer["payment_url"]),
            note="link_de_pagamento_enviado_sem_confirmacao_de_compra",
        ),
        now=created,
    )
    if case_id is None:
        return False
    settings = load_settings(conn, tenant_id)
    when = next_allowed(
        created + FIRST_FOLLOWUP,
        settings.timezone,
        settings.quiet_start,
        settings.quiet_end,
    )
    conn.execute(
        "UPDATE recovery_steps SET scheduled_at = %s, first_scheduled_at = %s "
        "WHERE tenant_id = %s AND case_id = %s AND step_no = 1",
        (when, created + FIRST_FOLLOWUP, tenant_id, case_id),
    )
    return True


def scan_confirmed_checkout_links(
    db: Database,
    *,
    now: datetime | None = None,
    limit: int = 60,
    pilot_scope: tuple[uuid.UUID, str] | None = None,
    pilot_exclusion: tuple[uuid.UUID, str] | None = None,
) -> int:
    """Reconcile confirmed links even if the webhook arrived before the worker ACK.

    Only recent, not-yet-answered bot messages qualify. Old links, uncertain
    sends and replied-to conversations are ignored; scanning is idempotent.
    """
    now = now or datetime.now(UTC)
    with db.tx(system=True) as conn:
        candidates = conn.execute(
            "SELECT m.tenant_id, m.id FROM messages m "
            "JOIN conversations cv ON cv.id = m.conversation_id AND cv.tenant_id = m.tenant_id "
            "JOIN contacts ct ON ct.id = cv.contact_id AND ct.tenant_id = m.tenant_id "
            "JOIN tenant_settings s ON s.tenant_id = m.tenant_id "
            "AND s.recovery_enabled AND s.consent_declared_at IS NOT NULL "
            "WHERE m.direction = 'out' AND m.author = 'bot' "
            "AND m.status IN ('sent', 'delivered', 'read') "
            "AND m.provider_message_id IS NOT NULL "
            "AND m.created_at BETWEEN %s AND %s "
            "AND m.body LIKE '%%https://%%' "
            "AND cv.channel = 'whatsapp' AND cv.last_inbound_at <= m.created_at "
            "AND (%s::uuid IS NULL OR (m.tenant_id = %s AND ct.phone = %s)) "
            "AND (%s::uuid IS NULL OR m.tenant_id <> %s OR ct.phone = %s) "
            "AND NOT EXISTS (SELECT 1 FROM recovery_cases rc "
            "WHERE rc.tenant_id = m.tenant_id AND rc.trigger_kind = 'conversation_cold' "
            "AND rc.external_ref = 'offer-link:' || m.id::text) "
            "ORDER BY m.created_at DESC LIMIT %s",
            (
                now - MAX_LOOKBACK,
                now,
                pilot_scope[0] if pilot_scope else None,
                pilot_scope[0] if pilot_scope else None,
                pilot_scope[1] if pilot_scope else None,
                pilot_exclusion[0] if pilot_exclusion else None,
                pilot_exclusion[0] if pilot_exclusion else None,
                pilot_exclusion[1] if pilot_exclusion else None,
                limit,
            ),
        ).fetchall()
    opened = 0
    for row in candidates:
        with db.tx(tenant_id=row["tenant_id"]) as conn:
            opened += bool(
                record_confirmed_checkout_link(
                    conn, row["tenant_id"], row["id"], now=now
                )
            )
    return opened
