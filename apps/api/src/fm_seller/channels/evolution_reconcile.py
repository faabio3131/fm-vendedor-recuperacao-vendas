"""Durable read-only reconciliation of uncertain Evolution text sends.

A timeout or malformed ACK must never cause an automatic resend. Only a
unique matching record from the correct instance, phone, body and timestamp
may update the local message status and generate a follow-up opportunity.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from fm_seller.channels.evolution import EvolutionClient
from fm_seller.channels.evolution_delivery import find_confirmed_outbound
from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.recovery.checkout_link import record_confirmed_checkout_link

log = logging.getLogger("fm_seller.evolution_reconcile")


def reconcile_uncertain_sends(
    db: Database,
    settings: Settings,
    *,
    now: datetime | None = None,
    limit: int = 6,
) -> int:
    """Check at most `limit` provider records, with persisted 10-minute backoff."""
    if (
        not settings.evolution_enabled
        or not settings.evolution_send_enabled
        or settings.evolution_outbound_paused
    ):
        return 0
    now = now or datetime.now(UTC)
    with db.tx(system=True) as conn:
        rows = conn.execute(
            "UPDATE messages SET evolution_reconcile_checked_at = %s, "
            "evolution_reconcile_attempts = evolution_reconcile_attempts + 1 "
            "WHERE id IN (SELECT m.id FROM messages m "
            "JOIN conversations cv ON cv.id=m.conversation_id AND cv.tenant_id=m.tenant_id "
            "JOIN tenants t ON t.id=m.tenant_id AND t.status='active' "
            "WHERE m.direction='out' AND m.author='bot' AND m.status='uncertain' "
            "AND m.outbound_kind='text' "
            "AND m.provider_message_id IS NULL AND cv.channel='whatsapp' "
            "AND m.created_at BETWEEN %s AND %s "
            "AND (m.evolution_reconcile_checked_at IS NULL "
            "OR m.evolution_reconcile_checked_at <= %s) "
            "AND EXISTS(SELECT 1 FROM connections cc "
            "WHERE cc.tenant_id=m.tenant_id AND cc.provider='evolution' "
            "AND cc.status='connected') "
            "ORDER BY m.created_at DESC LIMIT %s FOR UPDATE OF m SKIP LOCKED) "
            "RETURNING tenant_id,id",
            (now, now - timedelta(hours=24), now, now - timedelta(minutes=10), limit),
        ).fetchall()
    if not rows:
        return 0
    client = EvolutionClient(
        base_url=settings.evolution_api_base_url,
        api_key=settings.evolution_api_key.get_secret_value(),
        timeout=settings.evolution_timeout_seconds,
    )
    confirmed = 0
    for row in rows:
        tenant_id: uuid.UUID = row["tenant_id"]
        message_id: uuid.UUID = row["id"]
        try:
            with db.tx(tenant_id=tenant_id) as conn:
                item = conn.execute(
                    "SELECT m.body,m.created_at,ct.phone FROM messages m "
                    "JOIN conversations cv ON cv.id=m.conversation_id "
                    "JOIN contacts ct ON ct.id=cv.contact_id "
                    "WHERE m.tenant_id=%s AND m.id=%s AND m.status='uncertain' "
                    "AND m.provider_message_id IS NULL AND cv.channel='whatsapp'",
                    (tenant_id, message_id),
                ).fetchone()
            if item is None or not item["phone"]:
                continue
            instance = "fmav-" + hashlib.sha256(tenant_id.bytes).hexdigest()[:32]
            provider_id = find_confirmed_outbound(
                client,
                instance,
                str(item["phone"]),
                str(item["body"]),
                sent_at=item["created_at"].timestamp(),
            )
            if provider_id is None:
                continue
            with db.tx(tenant_id=tenant_id) as conn:
                changed = conn.execute(
                    "UPDATE messages SET status='sent',error=NULL,provider_message_id=%s "
                    "WHERE tenant_id=%s AND id=%s AND status='uncertain' "
                    "AND provider_message_id IS NULL",
                    (provider_id, tenant_id, message_id),
                ).rowcount
                if changed == 1:
                    record_confirmed_checkout_link(conn, tenant_id, message_id, now=now)
                    confirmed += 1
        except Exception:
            # Keep uncertain, retry after backoff; never initiate another send.
            log.exception("reconciliação Evolution inconclusiva")
    return confirmed
