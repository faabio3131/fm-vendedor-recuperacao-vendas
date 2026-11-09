"""Payment webhook reconciliation: authenticate fast, verify on provider server-side.

No webhook body alone can mark an order paid. Each event is durable, leased
and reconciled via the merchant's own provider API before state transition.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.merchant.asaas import AsaasClient
from fm_seller.merchant.service import MerchantService
from fm_seller.security.crypto import SecretBox

log = logging.getLogger("fm_seller.merchant.worker")


@dataclass
class PaymentEventStats:
    processed: int = 0
    retried: int = 0
    review: int = 0


def process_payment_events(
    db: Database,
    box: SecretBox,
    settings: Settings,
    *,
    client: AsaasClient | None = None,
    limit: int = 30,
) -> PaymentEventStats:
    provider = client or AsaasClient()
    service = MerchantService(db, box, settings, provider=provider)
    stats = PaymentEventStats()
    for _ in range(max(0, min(limit, 100))):
        with db.tx(system=True) as conn:
            row = conn.execute(
                "SELECT id,tenant_id,provider,provider_payment_id,attempts "
                "FROM merchant_payment_events "
                "WHERE ((disposition='queued' AND (lease_until IS NULL OR lease_until<=now())) "
                "OR (disposition='processing' AND lease_until<now())) "
                "AND attempts < 12 ORDER BY created_at "
                "FOR UPDATE SKIP LOCKED LIMIT 1"
            ).fetchone()
            if row is None:
                break
            conn.execute(
                "UPDATE merchant_payment_events SET disposition='processing', "
                "attempts=attempts+1,lease_until=now()+interval '3 minutes' "
                "WHERE id=%s",
                (row["id"],),
            )
        result = "review"
        try:
            with db.tx(tenant_id=row["tenant_id"]) as conn:
                account, keys = service._account(conn, row["tenant_id"], row["provider"])
                # Never perform a network request while holding a DB transaction.
                mode, token = account["mode"], str(keys["access_token"])
            gateway = service._client(account["provider"])
            actual = gateway.get_charge(mode, token, row["provider_payment_id"])
            result = service.apply_status(row["tenant_id"], actual)
        except Exception as exc:
            # Fail closed. Avoid PII, API tokens and raw provider responses in logs.
            log.warning("Financial event deferred: %s", type(exc).__name__)
            result = "retry"
        if result in ("retry", "unknown_charge"):
            with db.tx(tenant_id=row["tenant_id"]) as conn:
                if row["attempts"] + 1 < 12:
                    conn.execute(
                        "UPDATE merchant_payment_events SET disposition='queued',"
                        "lease_until=now()+interval '2 minutes' WHERE id=%s",
                        (row["id"],),
                    )
                    stats.retried += 1
                else:
                    conn.execute(
                        "UPDATE merchant_payment_events SET disposition='review',"
                        "lease_until=NULL,processed_at=now() WHERE id=%s",
                        (row["id"],),
                    )
                    stats.review += 1
        else:
            with db.tx(tenant_id=row["tenant_id"]) as conn:
                conn.execute(
                    "UPDATE merchant_payment_events SET disposition=%s,"
                    "lease_until=NULL,processed_at=now() WHERE id=%s",
                    (
                        "review"
                        if result == "review"
                        else "ignored"
                        if result in ("ignored", "terminal")
                        else "applied",
                        row["id"],
                    ),
                )
            if result == "review":
                stats.review += 1
            else:
                stats.processed += 1
    return stats
