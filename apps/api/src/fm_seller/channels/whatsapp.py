"""Entrada do WhatsApp Cloud API: verificação do webhook, assinatura, mensagens e status.

Estrutura do payload conforme a documentação da Meta; AINDA NÃO conferida com uma conta real.
Regras: nada sem assinatura válida (o segredo do app Meta é obrigatório); mensagem repetida
(mesmo id) não duplica efeito; o estado de entrega vem do provedor, não da intenção.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from collections.abc import Mapping
from typing import Any

from fm_seller.channels.inbound import OPT_OUT_REPLY, record_inbound
from fm_seller.channels.outbound import enqueue_text
from fm_seller.db import Conn, Database
from fm_seller.errors import AppError, bad_request, not_found
from fm_seller.events.normalize import normalize_phone_br
from fm_seller.security.crypto import CryptoError, SecretBox

log = logging.getLogger("fm_seller.whatsapp")
PROVIDER = "whatsapp_cloud"
MAX_BODY = 1024 * 1024
__all__ = ["OPT_OUT_REPLY", "enqueue_text"]  # reexportados: o resto do código importa daqui
_ORDER = ["queued", "sending", "sent", "delivered", "read"]


def load_connection(
    db: Database, box: SecretBox, public_id: str, provider: str
) -> tuple[uuid.UUID, uuid.UUID, dict[str, Any], str]:
    with db.tx(system=True) as conn:
        row = conn.execute(
            "SELECT c.id, c.tenant_id, c.config_encrypted, t.status AS tenant_status "
            "FROM connections c JOIN tenants t ON t.id = c.tenant_id "
            "WHERE c.public_id = %s AND c.provider = %s",
            (public_id, provider),
        ).fetchone()
    if row is None:
        raise not_found("Conexão não encontrada.")
    try:
        config = box.decrypt(
            row["config_encrypted"], tenant_id=str(row["tenant_id"]), provider=provider
        )
    except CryptoError:
        raise AppError(500, "credentials_unreadable", "Configuração da conexão inválida.") from None
    return row["id"], row["tenant_id"], config, row["tenant_status"]


def verify_handshake(
    db: Database,
    box: SecretBox,
    public_id: str,
    mode: str,
    token: str,
    challenge: str,
    provider: str = PROVIDER,
) -> str:
    """GET de verificação da Meta: devolve o desafio só se o token bate."""
    _, _, config, _ = load_connection(db, box, public_id, provider)
    expected = str(config.get("webhook_secret", ""))
    ok = bool(expected) and hmac.compare_digest(token.encode(), expected.encode())
    if mode != "subscribe" or not ok:
        raise AppError(403, "invalid_verify_token", "Token de verificação inválido.")
    return challenge


def signature_ok(app_secret: str, raw: bytes, header: str) -> bool:
    if not app_secret or not header.startswith("sha256="):
        return False
    digest = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest.encode(), header.removeprefix("sha256=").encode())


def ingest_whatsapp(
    db: Database,
    box: SecretBox,
    *,
    public_id: str,
    headers: Mapping[str, str],
    raw_body: bytes,
) -> dict[str, int]:
    if len(raw_body) > MAX_BODY:
        raise AppError(413, "payload_too_large", "Corpo grande demais.")
    connection_id, tenant_id, config, tenant_status = load_connection(db, box, public_id, PROVIDER)
    app_secret = str(config.get("app_secret", ""))
    if not app_secret:
        raise AppError(401, "app_secret_missing", "Informe o segredo do app Meta na conexão.")
    h = {k.lower(): v for k, v in headers.items()}
    if not signature_ok(app_secret, raw_body, h.get("x-hub-signature-256", "")):
        raise AppError(401, "invalid_signature", "Assinatura inválida.")
    if tenant_status != "active":
        raise AppError(403, "tenant_suspended", "Cliente suspenso.")
    try:
        body: Any = json.loads(raw_body)
    except ValueError as exc:
        raise bad_request("invalid_json", "Corpo não é JSON válido.") from exc
    if not isinstance(body, dict):
        raise bad_request("invalid_json", "Corpo precisa ser um objeto JSON.")

    counts = {"messages": 0, "statuses": 0, "opt_outs": 0, "duplicates": 0}
    number_id = str(config.get("phone_number_id", ""))
    with db.tx(tenant_id=tenant_id) as conn:
        for entry in body.get("entry", []) or []:
            for change in (entry or {}).get("changes", []) or []:
                value = (change or {}).get("value") or {}
                meta = value.get("metadata") or {}
                if number_id and str(meta.get("phone_number_id", number_id)) != number_id:
                    continue  # evento de outro número, não deste cliente
                names = {
                    str(c.get("wa_id", "")): str((c.get("profile") or {}).get("name", ""))
                    for c in value.get("contacts", []) or []
                }
                for msg in value.get("messages", []) or []:
                    _inbound(conn, tenant_id, msg, names, counts)
                for st in value.get("statuses", []) or []:
                    _status(conn, tenant_id, st, counts)
        conn.execute(
            "UPDATE connections SET status = 'connected', last_error = NULL, "
            "last_verified_at = now() WHERE id = %s",
            (connection_id,),
        )
    return counts


def _text_of(msg: Mapping[str, Any]) -> str:
    kind = str(msg.get("type", ""))
    if kind == "text":
        return str((msg.get("text") or {}).get("body", ""))[:4000]
    if kind == "button":
        return str((msg.get("button") or {}).get("text", ""))[:4000]
    if kind == "interactive":
        inter = msg.get("interactive") or {}
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        return str(reply.get("title", ""))[:4000]
    return f"[mensagem do tipo {kind or 'desconhecido'}]"


def upsert_conversation(
    conn: Conn, tenant_id: uuid.UUID, phone: str, name: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """Devolve (contato, conversa) para o telefone, criando o que faltar."""
    conn.execute(
        "INSERT INTO contacts (tenant_id, name, phone) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (tenant_id, name[:200], phone),
    )
    contact = conn.execute(
        "SELECT id FROM contacts WHERE tenant_id = %s AND phone = %s", (tenant_id, phone)
    ).fetchone()
    assert contact is not None
    conn.execute(
        "UPDATE contacts SET name = %s WHERE id = %s AND name = ''", (name[:200], contact["id"])
    )
    conn.execute(
        "INSERT INTO conversations (tenant_id, contact_id) VALUES (%s, %s) "
        "ON CONFLICT (tenant_id, contact_id, channel) DO NOTHING",
        (tenant_id, contact["id"]),
    )
    conv = conn.execute(
        "SELECT id FROM conversations WHERE tenant_id = %s AND contact_id = %s "
        "AND channel = 'whatsapp'",
        (tenant_id, contact["id"]),
    ).fetchone()
    assert conv is not None
    return contact["id"], conv["id"]


def _inbound(
    conn: Conn,
    tenant_id: uuid.UUID,
    msg: Mapping[str, Any],
    names: Mapping[str, str],
    counts: dict[str, int],
) -> None:
    phone = normalize_phone_br(msg.get("from"))
    message_id = str(msg.get("id", ""))
    if not phone or not message_id:
        return
    contact_id, conv_id = upsert_conversation(
        conn, tenant_id, phone, names.get(str(msg.get("from")), "")
    )
    record_inbound(
        conn,
        tenant_id,
        contact_id=contact_id,
        conv_id=conv_id,
        identity=phone,
        text=_text_of(msg),
        message_id=message_id,
        counts=counts,
    )


def _status(
    conn: Conn, tenant_id: uuid.UUID, st: Mapping[str, Any], counts: dict[str, int]
) -> None:
    pid, status = str(st.get("id", "")), str(st.get("status", ""))
    if not pid or status not in ("sent", "delivered", "read", "failed"):
        return
    error = None
    if status == "failed":
        errs = st.get("errors") or [{}]
        error = (str(errs[0].get("title", "")) or "falha informada pelo WhatsApp")[:200]
    conn.execute(
        "UPDATE messages SET status = %s, error = %s WHERE tenant_id = %s "
        "AND provider_message_id = %s AND direction = 'out' AND (%s = 'failed' OR "
        "array_position(%s::text[], status) < array_position(%s::text[], %s))",
        (status, error, tenant_id, pid, status, _ORDER, _ORDER, status),
    )
    conn.execute(
        "UPDATE recovery_steps SET delivery_status = %s, delivery_error = %s "
        "WHERE tenant_id = %s AND provider_message_id = %s AND (%s = 'failed' OR "
        "coalesce(array_position(%s::text[], delivery_status), 0) < "
        "array_position(%s::text[], %s))",
        (status, error, tenant_id, pid, status, _ORDER, _ORDER, status),
    )
    counts["statuses"] += 1
