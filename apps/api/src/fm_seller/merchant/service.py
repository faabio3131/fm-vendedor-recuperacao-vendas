"""Merchant orders: deterministic catalog pricing, per-tenant Asaas settlement.

No sensitive card numbers, CPF or provider secrets are persisted in orders or
returned to the browser. Provider sends funds directly to merchant's account.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fm_seller.config import Settings
from fm_seller.db import Conn, Database
from fm_seller.errors import AppError, bad_request, forbidden, not_found
from fm_seller.merchant.asaas import (
    AsaasClient,
    ProviderRejected,
    ProviderUncertain,
    safe_invoice_url,
    verify_charge,
)
from fm_seller.security.crypto import SecretBox
from fm_seller.services import EDIT_ROLES, Principal, audit, tenant_features

log = logging.getLogger("fm_seller.merchant")
MAX_ITEMS = 20
MAX_TOTAL_CENTS = 10_000_000
PROVIDER_PENDING = {"PENDING", "OVERDUE", "AWAITING_RISK_ANALYSIS", "AUTHORIZED"}
PROVIDER_PAID = {"CONFIRMED", "RECEIVED"}
PROVIDER_REFUND = {"REFUNDED"}
PROVIDER_DISPUTE = {"CHARGEBACK_REQUESTED", "CHARGEBACK_DISPUTE"}
PROVIDER_REVIEW = {"PARTIALLY_REFUNDED", "REFUND_IN_PROGRESS", "CHARGEBACK_REVERSAL"}


def _text(value: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise bad_request("invalid_field", "Campo obrigatório inválido.")
    return value.strip()


def _cpf_valid(value: str) -> bool:
    numbers = re.sub(r"\D", "", value)
    if len(numbers) != 11 or len(set(numbers)) == 1:
        return False
    for i in (9, 10):
        result = (sum(int(v) * (i + 1 - j) for j, v in enumerate(numbers[:i])) * 10) % 11
        if result == 10:
            result = 0
        if result != int(numbers[i]):
            return False
    return True


def _cnpj_valid(value: str) -> bool:
    numbers = re.sub(r"\D", "", value)
    if len(numbers) != 14 or len(set(numbers)) == 1:
        return False
    for i, weights in (
        (12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
        (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
    ):
        residue = sum(int(x) * w for x, w in zip(numbers[:i], weights, strict=True)) % 11
        if (11 - residue if residue >= 2 else 0) != int(numbers[i]):
            return False
    return True


def _only_tax_id(raw: str) -> str:
    if len(raw) > 25:
        raise bad_request("invalid_tax_id", "CPF/CNPJ inválido.")
    result = re.sub(r"\D", "", raw)
    if not (_cpf_valid(result) or _cnpj_valid(result)):
        raise bad_request("invalid_tax_id", "CPF/CNPJ inválido.")
    return result


def _order_data(row: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "customer_name": row["customer_name"],
        "customer_phone": row["customer_phone"],
        "customer_email": row["customer_email"],
        "items_cents": row["items_cents"],
        "delivery_fee_cents": row["delivery_fee_cents"],
        "total_cents": row["total_cents"],
        "created_at": row["created_at"],
        "paid_at": row["paid_at"],
        "settled_at": row["settled_at"],
        "items": [
            {
                "name": x["product_name"],
                "quantity": x["quantity"],
                "unit_price_cents": x["unit_price_cents"],
                "line_cents": x["line_cents"],
            }
            for x in items
        ],
        "charge": (
            {
                "status": row["charge_status"],
                "invoice_url": row["invoice_url"],
                "mode": row["mode"],
                "provider": row["provider"],
            }
            if row.get("charge_status")
            else None
        ),
    }


class MerchantService:
    def __init__(
        self,
        db: Database,
        box: SecretBox,
        settings: Settings,
        provider: AsaasClient | None = None,
    ) -> None:
        self.db, self.box, self.settings = db, box, settings
        self.provider = provider or AsaasClient()

    def guard(self, p: Principal, *, edit: bool = False) -> None:
        _, _, features = tenant_features(self.db, p)
        if "ai.seller" not in features:
            raise AppError(403, "plan_required", "Seu plano atual não permite pedidos financeiros.")
        if edit and p.role not in EDIT_ROLES:
            raise forbidden("Só proprietário e administrador podem operar cobranças.")

    def _account(self, conn: Conn, tenant_id: uuid.UUID) -> tuple[dict[str, Any], dict[str, Any]]:
        row = conn.execute(
            "SELECT * FROM merchant_accounts WHERE tenant_id=%s", (tenant_id,)
        ).fetchone()
        if row is None or row["verified_at"] is None:
            raise AppError(409, "merchant_not_ready", "Conecte e verifique sua conta Asaas.")
        credentials = self.box.decrypt(
            row["credentials_encrypted"], tenant_id=str(tenant_id), provider="merchant:asaas"
        )
        return row, credentials

    def get_account(self, p: Principal) -> dict[str, Any]:
        self.guard(p)
        with self.db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
            row = conn.execute(
                "SELECT mode,public_id,verified_at,delivery_fee_cents "
                "FROM merchant_accounts WHERE tenant_id=%s",
                (p.tenant_id,),
            ).fetchone()
        if not row:
            return {"provider": "asaas", "configured": False, "verified": False}
        return {
            "provider": "asaas",
            "configured": True,
            "verified": bool(row["verified_at"]),
            "mode": row["mode"],
            "delivery_fee_cents": row["delivery_fee_cents"],
            "webhook_url": self.settings.public_base_url.rstrip("/")
            + "/v1/webhooks/merchant/asaas/"
            + row["public_id"],
        }

    def configure(
        self,
        p: Principal,
        mode: str,
        access_token: str,
        webhook_token: str,
        delivery_fee_cents: int,
    ) -> dict[str, Any]:
        self.guard(p, edit=True)
        if mode not in ("sandbox", "production"):
            raise bad_request("payment_mode", "Ambiente financeiro inválido.")
        if len(access_token) < 20 or len(access_token) > 512:
            raise bad_request("access_token", "Chave da API inválida.")
        if not (32 <= len(webhook_token) <= 255) or any(c.isspace() for c in webhook_token):
            raise bad_request("webhook_token", "Use token independente com 32 a 255 caracteres.")
        if not (0 <= delivery_fee_cents <= 100_000):
            raise bad_request("delivery_fee", "Taxa de entrega inválida.")
        if hmac.compare_digest(access_token, webhook_token):
            raise bad_request("webhook_token", "Webhook precisa de um segredo diferente da API.")
        # Verification cannot be claimed from a simulated or failed response.
        try:
            verified = self.provider.verify_account(mode, access_token)
        except (ProviderRejected, ProviderUncertain):
            raise AppError(
                503, "provider_not_verified", "Não foi possível verificar a conta Asaas."
            ) from None
        if not verified:
            raise AppError(
                409,
                "merchant_unapproved",
                "A conta Asaas ainda não está aprovada para receber cobranças.",
            )
        encrypted = self.box.encrypt(
            {"access_token": access_token, "webhook_token": webhook_token},
            tenant_id=str(p.tenant_id),
            provider="merchant:asaas",
        )
        with self.db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("merchant-account:" + str(p.tenant_id),),
            )
            active = conn.execute(
                "SELECT count(*) AS count FROM merchant_charges ch "
                "JOIN merchant_orders o ON o.id=ch.order_id "
                "WHERE ch.tenant_id=%s AND o.status IN "
                "('charging','awaiting_payment','paid','disputed')",
                (p.tenant_id,),
            ).fetchone()
            if active and active["count"]:
                raise AppError(
                    409,
                    "merchant_account_locked",
                    "Não altere credenciais enquanto houver cobranças ou pagamentos vinculados.",
                )
            conn.execute(
                "INSERT INTO merchant_accounts(tenant_id,mode,credentials_encrypted,public_id,"
                "verified_at,delivery_fee_cents) VALUES(%s,%s,%s,%s,now(),%s) "
                "ON CONFLICT(tenant_id) DO UPDATE SET mode=excluded.mode, "
                "credentials_encrypted=excluded.credentials_encrypted, "
                "verified_at=now(), delivery_fee_cents=excluded.delivery_fee_cents, "
                "updated_at=now()",
                (p.tenant_id, mode, encrypted, secrets.token_urlsafe(24), delivery_fee_cents),
            )
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="merchant.account.configured",
                target="asaas",
                detail={"mode": mode},
            )
        return self.get_account(p)

    def create_order(
        self,
        p: Principal,
        idempotency_key: str,
        customer_name: str,
        customer_phone: str,
        customer_email: str,
        lines: list[tuple[uuid.UUID, int]],
    ) -> dict[str, Any]:
        self.guard(p, edit=True)
        key = _text(idempotency_key, 100)
        if len(key) < 12 or not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise bad_request("idempotency_key", "Identificador de operação inválido.")
        name = _text(customer_name, 120)
        phone = re.sub(r"\D", "", customer_phone)
        if phone and not (10 <= len(phone) <= 15):
            raise bad_request("phone", "Telefone inválido.")
        email = customer_email.strip().lower()
        if email and (len(email) > 254 or "@" not in email):
            raise bad_request("email", "E-mail inválido.")
        if not 1 <= len(lines) <= MAX_ITEMS:
            raise bad_request("items", "Selecione de 1 a 20 itens.")
        products = [(str(offer_id), qty) for offer_id, qty in lines]
        if len({k for k, _ in products}) != len(products):
            raise bad_request("duplicate_item", "Produto repetido; utilize quantidade.")
        if any(qty < 1 or qty > 99 for _, qty in products):
            raise bad_request("quantity", "Quantidade deve ser de 1 a 99.")
        raw = json.dumps(
            {"name": name, "phone": phone, "email": email, "items": sorted(products)},
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        with self.db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("merchant-order:" + str(p.tenant_id) + ":" + key,),
            )
            existing = conn.execute(
                "SELECT id,request_hash FROM merchant_orders "
                "WHERE tenant_id=%s AND idempotency_key=%s",
                (p.tenant_id, key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != fingerprint:
                    raise AppError(
                        409, "idempotency_conflict", "A mesma chave foi usada em outro pedido."
                    )
                return self.order(p, existing["id"])
            account, _ = self._account(conn, p.tenant_id)
            items = []
            sum_cents = 0
            for offer_id, qty in lines:
                row = conn.execute(
                    "SELECT id,name,price_cents FROM offers "
                    "WHERE tenant_id=%s AND id=%s AND active=true FOR SHARE",
                    (p.tenant_id, offer_id),
                ).fetchone()
                if row is None or row["price_cents"] <= 0:
                    raise bad_request("offer_invalid", "Produto não disponível ou sem preço.")
                sum_cents += row["price_cents"] * qty
                items.append((offer_id, row["name"], row["price_cents"], qty))
            if sum_cents <= 0 or sum_cents + account["delivery_fee_cents"] > MAX_TOTAL_CENTS:
                raise bad_request("order_total", "Valor fora do limite de segurança.")
            inserted = conn.execute(
                "INSERT INTO merchant_orders(tenant_id,idempotency_key,request_hash,"
                "customer_name,customer_phone,customer_email,items_cents,delivery_fee_cents)"
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (
                    p.tenant_id,
                    key,
                    fingerprint,
                    name,
                    phone,
                    email,
                    sum_cents,
                    account["delivery_fee_cents"],
                ),
            ).fetchone()
            assert inserted is not None
            oid = inserted["id"]
            for offer_id, product_name, price, qty in items:
                conn.execute(
                    "INSERT INTO merchant_order_items(tenant_id,order_id,offer_id,"
                    "product_name,unit_price_cents,quantity) VALUES(%s,%s,%s,%s,%s,%s)",
                    (p.tenant_id, oid, offer_id, product_name, price, qty),
                )
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="merchant.order.created",
                target=str(oid),
                detail={"total_cents": sum_cents + account["delivery_fee_cents"]},
            )
        return self.order(p, oid)

    def order(self, p: Principal, oid: uuid.UUID) -> dict[str, Any]:
        self.guard(p)
        with self.db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
            row = conn.execute(
                "SELECT o.*,c.status AS charge_status,c.invoice_url,c.provider,c.mode "
                "FROM merchant_orders o LEFT JOIN merchant_charges c "
                "ON c.order_id=o.id AND c.tenant_id=o.tenant_id "
                "WHERE o.tenant_id=%s AND o.id=%s",
                (p.tenant_id, oid),
            ).fetchone()
            if row is None:
                raise not_found("Pedido não encontrado.")
            items = conn.execute(
                "SELECT * FROM merchant_order_items WHERE tenant_id=%s AND order_id=%s "
                "ORDER BY product_name",
                (p.tenant_id, oid),
            ).fetchall()
        return _order_data(row, items)

    def orders(self, p: Principal) -> list[dict[str, Any]]:
        self.guard(p)
        with self.db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
            ids = conn.execute(
                "SELECT id FROM merchant_orders WHERE tenant_id=%s "
                "ORDER BY created_at DESC LIMIT 100",
                (p.tenant_id,),
            ).fetchall()
        return [self.order(p, x["id"]) for x in ids]

    def _bind_charge(self, p: Principal, oid: uuid.UUID, data: dict[str, Any]) -> None:
        with self.db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
            row = conn.execute(
                "SELECT id,external_reference,amount_cents,provider_payment_id "
                "FROM merchant_charges WHERE tenant_id=%s AND order_id=%s FOR UPDATE",
                (p.tenant_id, oid),
            ).fetchone()
            if row is None:
                raise not_found("Cobrança reservada não encontrada.")
            try:
                payment_id = verify_charge(
                    data,
                    row["external_reference"],
                    row["amount_cents"],
                    row["provider_payment_id"],
                )
                invoice = safe_invoice_url(data.get("invoiceUrl"))
            except (AppError, ProviderUncertain):
                payment_id = None
                invoice = None
            if payment_id is None or invoice is None:
                conn.execute(
                    "UPDATE merchant_charges SET status='review',updated_at=now() WHERE id=%s",
                    (row["id"],),
                )
                conn.execute(
                    "UPDATE merchant_orders SET status='review',updated_at=now() WHERE id=%s",
                    (oid,),
                )
                audit(
                    conn,
                    tenant_id=p.tenant_id,
                    actor=p.user_id,
                    action="merchant.charge.mismatch",
                    target=str(oid),
                )
                return
            conn.execute(
                "UPDATE merchant_charges SET provider_payment_id=%s,invoice_url=%s,"
                "status='pending',updated_at=now() WHERE id=%s",
                (payment_id, invoice, row["id"]),
            )
            conn.execute(
                "UPDATE merchant_orders SET status='awaiting_payment',updated_at=now() "
                "WHERE id=%s AND status='charging'",
                (oid,),
            )
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="merchant.charge.linked",
                target=str(oid),
            )

    def charge(self, p: Principal, oid: uuid.UUID, cpf_cnpj: str) -> dict[str, Any]:
        self.guard(p, edit=True)
        tax_id = _only_tax_id(cpf_cnpj)  # Never stored, never included in audit.
        with self.db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
            order = conn.execute(
                "SELECT * FROM merchant_orders WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (p.tenant_id, oid),
            ).fetchone()
            if order is None:
                raise not_found("Pedido não encontrado.")
            if order["status"] != "pending":
                raise AppError(409, "charge_exists", "Pedido já possui cobrança ou está encerrado.")
            account, config = self._account(conn, p.tenant_id)
            ref = "av-" + str(oid)
            conn.execute(
                "INSERT INTO merchant_charges(tenant_id,order_id,provider,mode,"
                "external_reference,amount_cents) VALUES(%s,%s,'asaas',%s,%s,%s)",
                (p.tenant_id, oid, account["mode"], ref, order["total_cents"]),
            )
            conn.execute(
                "UPDATE merchant_orders SET status='charging',updated_at=now() WHERE id=%s",
                (oid,),
            )
            mode, token = account["mode"], str(config["access_token"])
            name, cents = order["customer_name"], order["total_cents"]
        try:
            customer = self.provider.customer(mode, token, name, tax_id, ref)
            data = self.provider.create_charge(
                mode,
                token,
                customer,
                ref,
                cents,
                (datetime.now(UTC) + timedelta(days=2)).date().isoformat(),
            )
            self._bind_charge(p, oid, data)
        except (ProviderRejected, ProviderUncertain):
            with self.db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
                conn.execute(
                    "UPDATE merchant_charges SET status='unknown',updated_at=now() "
                    "WHERE tenant_id=%s AND order_id=%s AND status='creating'",
                    (p.tenant_id, oid),
                )
            raise AppError(
                503,
                "charge_uncertain",
                "Criação não confirmada. Não repita: consulte a conciliação do pedido.",
            ) from None
        return self.order(p, oid)

    def reconcile(self, p: Principal, oid: uuid.UUID) -> dict[str, Any]:
        self.guard(p, edit=True)
        with self.db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
            charge = conn.execute(
                "SELECT * FROM merchant_charges WHERE tenant_id=%s AND order_id=%s",
                (p.tenant_id, oid),
            ).fetchone()
            if charge is None:
                raise not_found("Cobrança não encontrada.")
            account, config = self._account(conn, p.tenant_id)
            if charge["mode"] != account["mode"]:
                raise AppError(409, "provider_mismatch", "Ambiente de cobrança divergente.")
            mode, key = account["mode"], str(config["access_token"])
        try:
            actual: dict[str, Any] | None
            if charge["provider_payment_id"]:
                actual = self.provider.get_charge(mode, key, charge["provider_payment_id"])
            else:
                actual = self.provider.find_charge(mode, key, charge["external_reference"])
            if actual is None:
                raise ProviderUncertain("Charge not found: cannot safely retry")
            if not charge["provider_payment_id"]:
                self._bind_charge(p, oid, actual)
            self.apply_status(
                p.tenant_id,
                actual,
                expected_oid=oid,
                actor=p.user_id,
            )
        except (ProviderRejected, ProviderUncertain):
            raise AppError(
                503,
                "reconcile_uncertain",
                "Não foi possível reconciliar. Nenhuma nova cobrança foi criada.",
            ) from None
        return self.order(p, oid)

    def apply_status(
        self,
        tenant_id: uuid.UUID,
        data: dict[str, Any],
        *,
        expected_oid: uuid.UUID | None = None,
        actor: uuid.UUID | None = None,
    ) -> str:
        with self.db.tx(tenant_id=tenant_id, user_id=actor) as conn:
            charge = conn.execute(
                "SELECT * FROM merchant_charges WHERE tenant_id=%s "
                "AND (provider_payment_id=%s OR "
                "(provider_payment_id IS NULL AND external_reference=%s)) FOR UPDATE",
                (tenant_id, data.get("id"), data.get("externalReference")),
            ).fetchone()
            if charge is None:
                return "unknown_charge"
            if expected_oid is not None and charge["order_id"] != expected_oid:
                raise AppError(409, "finance_mismatch", "Cobrança pertence a outro pedido.")
            try:
                verify_charge(
                    data,
                    charge["external_reference"],
                    charge["amount_cents"],
                    charge["provider_payment_id"],
                )
            except (AppError, ProviderUncertain):
                conn.execute(
                    "UPDATE merchant_charges SET status='review',updated_at=now() WHERE id=%s",
                    (charge["id"],),
                )
                conn.execute(
                    "UPDATE merchant_orders SET status='review',updated_at=now() WHERE id=%s",
                    (charge["order_id"],),
                )
                audit(
                    conn,
                    tenant_id=tenant_id,
                    actor=actor,
                    action="merchant.charge.mismatch",
                    target=str(charge["order_id"]),
                )
                return "review"
            order = conn.execute(
                "SELECT status FROM merchant_orders WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (tenant_id, charge["order_id"]),
            ).fetchone()
            if order is None:
                return "unknown_order"
            actual = str(data.get("status", ""))
            previous = order["status"]
            if previous in ("refunded", "disputed", "review", "canceled"):
                return "terminal"
            if actual in PROVIDER_REFUND:
                target = "refunded"
            elif actual in PROVIDER_DISPUTE:
                target = "disputed"
            elif actual in PROVIDER_REVIEW:
                target = "review"
            elif actual in PROVIDER_PAID:
                target = "paid"
            elif actual in PROVIDER_PENDING:
                target = "awaiting_payment" if previous != "paid" else "paid"
            else:
                return "ignored"
            conn.execute(
                "UPDATE merchant_orders SET status=%s,"
                "paid_at=CASE WHEN %s='paid' THEN COALESCE(paid_at,now()) ELSE paid_at END,"
                "settled_at=CASE WHEN %s='RECEIVED' THEN COALESCE(settled_at,now()) "
                "ELSE settled_at END, updated_at=now() WHERE id=%s",
                (target, target, actual, charge["order_id"]),
            )
            conn.execute(
                "UPDATE merchant_charges SET status=%s,updated_at=now() WHERE id=%s",
                ("pending" if target == "awaiting_payment" else target, charge["id"]),
            )
            if target != previous:
                audit(
                    conn,
                    tenant_id=tenant_id,
                    actor=actor,
                    action="merchant.payment.status",
                    target=str(charge["order_id"]),
                    detail={"from": previous, "to": target, "provider_status": actual},
                )
            return target

    def receive_event(self, public_id: str, provided_token: str, raw_body: bytes) -> str:
        if len(raw_body) > 64_000:
            raise AppError(413, "payload_too_large", "Evento financeiro muito grande.")
        with self.db.tx(system=True) as conn:
            account = conn.execute(
                "SELECT tenant_id,credentials_encrypted FROM merchant_accounts "
                "WHERE public_id=%s AND verified_at IS NOT NULL",
                (public_id,),
            ).fetchone()
        if account is None:
            raise not_found("Webhook financeiro não encontrado.")
        tenant_id = account["tenant_id"]
        secrets_ = self.box.decrypt(
            account["credentials_encrypted"],
            tenant_id=str(tenant_id),
            provider="merchant:asaas",
        )
        expected = str(secrets_.get("webhook_token", ""))
        if not expected or not provided_token or not hmac.compare_digest(expected, provided_token):
            raise AppError(401, "invalid_payment_webhook", "Autenticação do webhook inválida.")
        try:
            data = json.loads(raw_body)
        except ValueError:
            raise bad_request("bad_payment_event", "Evento financeiro inválido.") from None
        if not isinstance(data, dict):
            raise bad_request("bad_payment_event", "Evento financeiro inválido.")
        event_id = data.get("id")
        payment = data.get("payment")
        kind = data.get("event")
        if (
            not isinstance(event_id, str)
            or not 1 <= len(event_id) <= 160
            or not isinstance(payment, dict)
            or not isinstance(payment.get("id"), str)
            or not re.fullmatch(r"pay_[A-Za-z0-9_-]{1,90}", payment["id"])
            or not isinstance(kind, str)
            or not kind.startswith("PAYMENT_")
            or len(kind) > 100
        ):
            raise bad_request("bad_payment_event", "Evento financeiro inválido.")
        fingerprint = hashlib.sha256(raw_body).hexdigest()
        with self.db.tx(tenant_id=tenant_id) as conn:
            inserted = conn.execute(
                "INSERT INTO merchant_payment_events(tenant_id,provider,external_event_id,"
                "provider_payment_id,event_type,payload_sha256) "
                "VALUES(%s,'asaas',%s,%s,%s,%s) ON CONFLICT(tenant_id,external_event_id) "
                "DO NOTHING RETURNING id",
                (tenant_id, event_id, payment["id"], kind, fingerprint),
            ).fetchone()
            if inserted is None:
                old = conn.execute(
                    "SELECT payload_sha256 FROM merchant_payment_events "
                    "WHERE tenant_id=%s AND external_event_id=%s",
                    (tenant_id, event_id),
                ).fetchone()
                if old is None or old["payload_sha256"] != fingerprint:
                    raise AppError(
                        409, "webhook_conflict", "Identificador reutilizado com conteúdo diferente."
                    )
        return "accepted" if inserted else "duplicate"
