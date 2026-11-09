"""Async merchant checkout for customer-confirmed carts, never inside AI transaction.

A durable encrypted fiscal ID lives at most 24h and is removed after processing.
No blind retry on ambiguous provider mutation. Outbound checkout link is queued
in the SAME transaction that completes the job (no duplicated WhatsApp reply).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fm_seller.channels.outbound import enqueue_text
from fm_seller.channels.reply_policy import connected_whatsapp_provider
from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.merchant.asaas import AsaasClient
from fm_seller.merchant.gateways import LABELS
from fm_seller.merchant.service import MerchantService
from fm_seller.security.crypto import SecretBox

log = logging.getLogger("fm_seller.merchant.chat_worker")


def process_chat_charge_jobs(
    db: Database,
    box: SecretBox,
    settings: Settings,
    *,
    provider: AsaasClient | None = None,
    limit: int = 20,
) -> dict[str, int]:
    service = MerchantService(db, box, settings, provider=provider)
    result = {"ready": 0, "review": 0, "expired": 0}
    for _ in range(max(0, min(limit, 50))):
        with db.tx(system=True) as conn:
            job = conn.execute(
                "SELECT tenant_id,order_id,conversation_id,tax_id_encrypted,expires_at "
                "FROM merchant_chat_charge_jobs "
                "WHERE state='queued' OR (state='processing' AND lease_until<now()) "
                "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
            ).fetchone()
            if job is None:
                break
            conn.execute(
                "UPDATE merchant_chat_charge_jobs SET state='processing',"
                "attempts=attempts+1,lease_until=now()+interval '3 minutes' "
                "WHERE tenant_id=%s AND order_id=%s",
                (job["tenant_id"], job["order_id"]),
            )
        tenant, oid, cid = job["tenant_id"], job["order_id"], job["conversation_id"]
        message: str | None = None
        status = "review"
        try:
            with db.tx(tenant_id=tenant) as conn:
                row = conn.execute(
                    "SELECT o.status,c.invoice_url,c.provider,cv.status AS conv_status,"
                    "cv.last_inbound_at,ct.phone,t.status AS tenant_status "
                    "FROM merchant_orders o "
                    "LEFT JOIN merchant_charges c ON c.order_id=o.id AND c.tenant_id=o.tenant_id "
                    "JOIN conversations cv ON cv.tenant_id=o.tenant_id AND cv.id=%s "
                    "JOIN contacts ct ON ct.id=cv.contact_id AND ct.tenant_id=cv.tenant_id "
                    "JOIN tenants t ON t.id=o.tenant_id "
                    "WHERE o.tenant_id=%s AND o.id=%s",
                    (cid, tenant, oid),
                ).fetchone()
                tenant_settings = conn.execute(
                    "SELECT ai_enabled FROM tenant_settings WHERE tenant_id=%s",
                    (tenant,),
                ).fetchone()
                allowed = bool(
                    row
                    and row["tenant_status"] == "active"
                    and row["conv_status"] == "bot"
                    and row["last_inbound_at"] is not None
                    and datetime.now(UTC) - row["last_inbound_at"] <= timedelta(hours=24)
                    and connected_whatsapp_provider(conn, tenant) is not None
                    and tenant_settings is not None
                    and tenant_settings["ai_enabled"] is True
                    and not conn.execute(
                        "SELECT 1 FROM suppressions WHERE tenant_id=%s AND identity=%s",
                        (tenant, row["phone"] if row else ""),
                    ).fetchone()
                )
            if not allowed or job["expires_at"] <= datetime.now(UTC):
                status = "canceled"
            elif (
                row is not None
                and row["invoice_url"]
                and row["status"]
                in (
                    "awaiting_payment",
                    "paid",
                )
            ):
                message = (
                    None
                    if row["status"] == "paid"
                    else "Seu pedido está registrado. Pague somente neste link seguro:\n"
                    + str(row["invoice_url"])
                    + "\nAguardaremos a confirmação do "
                    + LABELS.get(str(row["provider"]), "provedor")
                    + "."
                )
                status = "done"
            elif row is not None and row["status"] == "pending":
                if job["tax_id_encrypted"] is None:
                    status = "review"
                else:
                    protected = box.decrypt(
                        job["tax_id_encrypted"],
                        tenant_id=str(tenant),
                        provider="merchant:chat-tax",
                    )
                    service._charge_for_tenant(tenant, oid, str(protected["cpf"]))
                    with db.tx(tenant_id=tenant) as conn:
                        charged = conn.execute(
                            "SELECT o.status,c.invoice_url,c.provider FROM merchant_orders o "
                            "JOIN merchant_charges c ON c.tenant_id=o.tenant_id "
                            "AND c.order_id=o.id WHERE o.tenant_id=%s AND o.id=%s",
                            (tenant, oid),
                        ).fetchone()
                    if (
                        charged
                        and charged["invoice_url"]
                        and charged["status"]
                        in (
                            "awaiting_payment",
                            "paid",
                        )
                    ):
                        message = (
                            None
                            if charged["status"] == "paid"
                            else "Seu pedido está registrado. Pague somente neste link seguro:\n"
                            + str(charged["invoice_url"])
                            + "\nAguardaremos a confirmação do "
                            + LABELS.get(str(charged["provider"]), "provedor")
                            + "."
                        )
                        status = "done"
        except Exception as exc:
            log.warning(
                "Checkout conversacional requer revisão: %s",
                type(exc).__name__,
            )
            status = "review"
        # Never expose tax ID or tokens in audit, logs, messages or retries.
        with db.tx(tenant_id=tenant) as conn:
            locked = conn.execute(
                "SELECT state FROM merchant_chat_charge_jobs "
                "WHERE tenant_id=%s AND order_id=%s FOR UPDATE",
                (tenant, oid),
            ).fetchone()
            if locked is None or locked["state"] != "processing":
                continue
            if status == "done" and message is not None:
                enqueue_text(conn, tenant, cid, "bot", message)
            if status in ("done", "review", "canceled"):
                conn.execute(
                    "UPDATE merchant_conversation_carts "
                    "SET stage='finished',draft='{}'::jsonb,items='[]'::jsonb,"
                    "updated_at=now() "
                    "WHERE tenant_id=%s AND conversation_id=%s AND order_id=%s",
                    (tenant, cid, oid),
                )
            conn.execute(
                "UPDATE merchant_chat_charge_jobs SET state=%s,"
                "tax_id_encrypted=NULL,lease_until=NULL WHERE tenant_id=%s AND order_id=%s",
                (status, tenant, oid),
            )
        if status == "done":
            result["ready"] += 1
        elif status == "canceled":
            result["expired"] += 1
        else:
            result["review"] += 1
    with db.tx(system=True) as conn:
        conn.execute(
            "UPDATE merchant_chat_charge_jobs SET tax_id_encrypted=NULL,"
            "state='canceled',lease_until=NULL "
            "WHERE state IN ('queued','processing') AND expires_at<now()"
        )
        conn.execute(
            "DELETE FROM merchant_conversation_carts c "
            "WHERE c.expires_at<now() AND NOT EXISTS "
            "(SELECT 1 FROM merchant_chat_charge_jobs j "
            "WHERE j.tenant_id=c.tenant_id AND j.order_id=c.order_id "
            "AND j.state IN ('queued','processing'))"
        )
    return result
