"""Authentic multi-gateway webhook intake. Webhook is a signal, never payment proof."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from fm_seller.errors import AppError, bad_request, not_found
from fm_seller.merchant.gateways import PROVIDERS, HostedGateway
from fm_seller.merchant.service import MerchantService


def _mercadopago_signature(
    secret: str,
    headers: Mapping[str, str],
    query: Mapping[str, str],
    data_id: str,
) -> bool:
    parts = {}
    for item in headers.get("x-signature", "").split(","):
        k, sep, v = item.strip().partition("=")
        if sep:
            parts[k.strip()] = v.strip()
    ts, signature = parts.get("ts", ""), parts.get("v1", "")
    request_id = headers.get("x-request-id", "")
    if not re.fullmatch(r"\d{10,13}", ts) or not re.fullmatch(r"[0-9a-f]{64}", signature):
        return False
    time_value = int(ts) / (1000 if len(ts) == 13 else 1)
    if abs(datetime.now(UTC).timestamp() - time_value) > 3600:
        return False
    signed_id = query.get("data.id") or query.get("data_id") or ""
    if signed_id != data_id or not signed_id or not request_id:
        return False
    manifest = f"id:{signed_id};request-id:{request_id};ts:{ts};"
    digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def _pagbank_signature(
    client: HostedGateway,
    mode: str,
    key: str,
    headers: Mapping[str, str],
    raw: bytes,
) -> bool:
    signatures = [p.strip() for p in headers.get("x-payload-signature", "").split(",") if p.strip()]
    if not signatures:
        return False
    try:
        row = client._request(mode, key, "GET", "/public-keys", params={"type": "webhook"})
        der = base64.b64decode(row["public_key"], validate=True)
        public = serialization.load_der_public_key(der)
    except (ValueError, KeyError, TypeError, base64.binascii.Error):
        return False
    if not isinstance(public, ec.EllipticCurvePublicKey):
        return False
    for signature in signatures:
        try:
            public.verify(
                base64.b64decode(signature, validate=True), raw, ec.ECDSA(hashes.SHA256())
            )
            return True
        except (InvalidSignature, ValueError, base64.binascii.Error):
            continue
    return False


def receive_gateway_event(
    service: MerchantService,
    provider: str,
    public_id: str,
    headers: Mapping[str, str],
    query: Mapping[str, str],
    raw: bytes,
) -> str:
    if provider not in PROVIDERS or provider == "asaas":
        raise not_found("Webhook financeiro não encontrado.")
    if len(raw) > 64_000:
        raise AppError(413, "payload_too_large", "Evento financeiro excede o limite.")
    with service.db.tx(system=True) as conn:
        account = conn.execute(
            "SELECT tenant_id,credentials_encrypted,mode FROM merchant_accounts "
            "WHERE provider=%s AND public_id=%s AND verified_at IS NOT NULL",
            (provider, public_id),
        ).fetchone()
    if account is None:
        raise not_found("Webhook financeiro não encontrado.")
    tenant = account["tenant_id"]
    credentials = service.box.decrypt(
        account["credentials_encrypted"],
        tenant_id=str(tenant),
        provider="merchant:" + provider,
    )
    secret = str(credentials.get("webhook_token", ""))
    if not secret:
        raise AppError(401, "invalid_payment_webhook", "Autenticação inválida.")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise bad_request("invalid_payment_event", "Evento inválido.") from None
    if not isinstance(body, dict):
        raise bad_request("invalid_payment_event", "Evento inválido.")
    payload = body.get("data") if isinstance(body.get("data"), dict) else {}
    pid: str | None = None
    reference: str | None = None
    if provider == "mercadopago":
        pid = str(payload.get("id", ""))
        if not _mercadopago_signature(secret, headers, query, pid):
            raise AppError(401, "invalid_payment_webhook", "Assinatura Mercado Pago inválida.")
        if body.get("type") not in ("order", "orders_v2"):
            return "ignored"
    elif provider == "pagbank":
        client = HostedGateway(provider)
        if not _pagbank_signature(
            client, account["mode"], str(credentials["access_token"]), headers, raw
        ):
            raise AppError(401, "invalid_payment_webhook", "Assinatura PagBank inválida.")
        candidate = headers.get("x-product-id") or body.get("id") or ""
        pid = str(candidate) if str(candidate).startswith("CHEC_") else None
        reference = body.get("reference_id")
    elif provider == "picpay":
        token = headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(secret, token):
            raise AppError(401, "invalid_payment_webhook", "Autenticação PicPay inválida.")
        charge = payload.get("charge") if isinstance(payload.get("charge"), dict) else {}
        pid = str(charge.get("paymentLinkId") or "") or None
    else:
        # Pagar.me v5 has no documented signed webhook contract on this endpoint.
        # The 192-bit, unguessable account URL is only an event hint. The worker
        # ALWAYS re-queries the merchant's Pagar.me API; never trust body status.
        row = payload.get("payment_link") if isinstance(payload.get("payment_link"), dict) else {}
        pid = str(row.get("id") or payload.get("payment_link_id") or "") or None
        reference = payload.get("order_code") or body.get("order_code")
    if pid and not re.fullmatch(r"[a-zA-Z0-9_-]{3,100}", pid):
        raise bad_request("bad_payment_event", "ID de cobrança inválido.")
    with service.db.tx(tenant_id=tenant) as conn:
        if pid:
            charge = conn.execute(
                "SELECT provider_payment_id FROM merchant_charges "
                "WHERE tenant_id=%s AND provider=%s AND provider_payment_id=%s",
                (tenant, provider, pid),
            ).fetchone()
        else:
            charge = None
        if charge is None and isinstance(reference, str):
            charge = conn.execute(
                "SELECT provider_payment_id FROM merchant_charges "
                "WHERE tenant_id=%s AND provider=%s AND external_reference=%s",
                (tenant, provider, reference),
            ).fetchone()
        if charge is None or not charge["provider_payment_id"]:
            # Uncorrelated hints cannot confer ownership or payment status.
            return "ignored"
        safe_id = str(charge["provider_payment_id"])
        fingerprint = hashlib.sha256(raw).hexdigest()
        # Hash = stable identity when provider lacks a unique notification ID.
        eid = str(body.get("id") or fingerprint)
        if not 1 <= len(eid) <= 160:
            eid = fingerprint
        kind = str(body.get("type") or body.get("event") or "payment.updated")[:100]
        inserted = conn.execute(
            "INSERT INTO merchant_payment_events(tenant_id,provider,external_event_id,"
            "provider_payment_id,event_type,payload_sha256) VALUES(%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(tenant_id,provider,external_event_id) DO NOTHING RETURNING id",
            (tenant, provider, eid, safe_id, kind, fingerprint),
        ).fetchone()
        if inserted is None:
            old = conn.execute(
                "SELECT payload_sha256 FROM merchant_payment_events "
                "WHERE tenant_id=%s AND provider=%s AND external_event_id=%s",
                (tenant, provider, eid),
            ).fetchone()
            if old is None or old["payload_sha256"] != fingerprint:
                raise AppError(409, "webhook_conflict", "Evento repetido com conteúdo divergente.")
    return "accepted" if inserted else "duplicate"
