"""Entrada do Messenger e do Instagram (mensagens diretas): assinatura, mensagens e entrega.

Estrutura do payload conforme a documentação da Meta (`entry[].messaging[]`); AINDA NÃO conferida
com uma conta real. Regras iguais às do WhatsApp: nada sem assinatura válida (o segredo do app
Meta é obrigatório); mensagem repetida (mesmo id) não duplica efeito; o estado de entrega vem do
provedor; eco das mensagens enviadas por nós é ignorado.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fm_seller.channels.inbound import record_inbound
from fm_seller.channels.whatsapp import load_connection, signature_ok
from fm_seller.db import Conn, Database
from fm_seller.errors import AppError, bad_request
from fm_seller.security.crypto import SecretBox

MAX_BODY = 1024 * 1024
_ORDER = ["queued", "sending", "sent", "delivered", "read"]


@dataclass(frozen=True)
class SocialChannel:
    provider: str  # chave da conexão e do webhook
    channel: str  # valor em conversations.channel
    label: str
    id_field: str  # campo da conexão com o ID da página/conta
    token_field: str  # campo da conexão com o token de acesso


SOCIAL: dict[str, SocialChannel] = {
    "messenger": SocialChannel(
        "messenger", "messenger", "Messenger", "page_id", "page_access_token"
    ),
    "instagram_dm": SocialChannel(
        "instagram_dm", "instagram", "Instagram", "instagram_account_id", "access_token"
    ),
}
BY_CHANNEL = {c.channel: c for c in SOCIAL.values()}


def identity_of(channel: str, external_id: str) -> str:
    """Identidade usada no bloqueio de contato (tabela `suppressions`)."""
    return f"{channel}:{external_id}"


def upsert_social_conversation(
    conn: Conn, tenant_id: uuid.UUID, channel: str, external_id: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """Devolve (contato, conversa) para o ID do canal, criando o que faltar."""
    row = conn.execute(
        "SELECT contact_id FROM contact_channels "
        "WHERE tenant_id = %s AND channel = %s AND external_id = %s",
        (tenant_id, channel, external_id),
    ).fetchone()
    if row is None:
        contact = conn.execute(
            "INSERT INTO contacts (tenant_id, name, channel_only) VALUES (%s, '', true) "
            "RETURNING id",
            (tenant_id,),
        ).fetchone()
        assert contact is not None
        conn.execute(
            "INSERT INTO contact_channels (tenant_id, contact_id, channel, external_id) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (tenant_id, contact["id"], channel, external_id),
        )
        row = conn.execute(
            "SELECT contact_id FROM contact_channels "
            "WHERE tenant_id = %s AND channel = %s AND external_id = %s",
            (tenant_id, channel, external_id),
        ).fetchone()
        assert row is not None
    contact_id = row["contact_id"]
    conn.execute(
        "INSERT INTO conversations (tenant_id, contact_id, channel) VALUES (%s, %s, %s) "
        "ON CONFLICT (tenant_id, contact_id, channel) DO NOTHING",
        (tenant_id, contact_id, channel),
    )
    conv = conn.execute(
        "SELECT id FROM conversations WHERE tenant_id = %s AND contact_id = %s AND channel = %s",
        (tenant_id, contact_id, channel),
    ).fetchone()
    assert conv is not None
    return contact_id, conv["id"]


def _text_of(event: Mapping[str, Any]) -> str:
    msg = event.get("message") or {}
    if msg.get("text"):
        return str(msg["text"])[:4000]
    quick = (msg.get("quick_reply") or {}).get("payload")
    if quick:
        return str(quick)[:4000]
    postback = event.get("postback") or {}
    if postback.get("title"):
        return str(postback["title"])[:4000]
    kinds = sorted({str(a.get("type", "")) for a in msg.get("attachments") or [] if a})
    return f"[mensagem do tipo {kinds[0] if kinds else 'desconhecido'}]"


def ingest_social(
    db: Database,
    box: SecretBox,
    *,
    provider: str,
    public_id: str,
    headers: Mapping[str, str],
    raw_body: bytes,
) -> dict[str, int]:
    ch = SOCIAL[provider]
    if len(raw_body) > MAX_BODY:
        raise AppError(413, "payload_too_large", "Corpo grande demais.")
    connection_id, tenant_id, config, tenant_status = load_connection(db, box, public_id, provider)
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
    own_id = str(config.get(ch.id_field, ""))
    with db.tx(tenant_id=tenant_id) as conn:
        for entry in body.get("entry", []) or []:
            entry = entry or {}
            if own_id and str(entry.get("id", own_id)) != own_id:
                continue  # evento de outra página/conta, não deste cliente
            for event in entry.get("messaging", []) or []:
                _event(conn, tenant_id, ch, own_id, event or {}, counts)
        conn.execute(
            "UPDATE connections SET status = 'connected', last_error = NULL, "
            "last_verified_at = now() WHERE id = %s",
            (connection_id,),
        )
    return counts


def _event(
    conn: Conn,
    tenant_id: uuid.UUID,
    ch: SocialChannel,
    own_id: str,
    event: Mapping[str, Any],
    counts: dict[str, int],
) -> None:
    sender = str((event.get("sender") or {}).get("id", ""))
    msg = event.get("message")
    if isinstance(msg, dict):
        if msg.get("is_echo") or (own_id and sender == own_id):
            return  # eco do que nós mesmos enviamos
        message_id = str(msg.get("mid", ""))
        if not sender or not message_id:
            return
        contact_id, conv_id = upsert_social_conversation(conn, tenant_id, ch.channel, sender)
        record_inbound(
            conn,
            tenant_id,
            contact_id=contact_id,
            conv_id=conv_id,
            identity=identity_of(ch.channel, sender),
            text=_text_of(event),
            message_id=message_id,
            counts=counts,
        )
        return
    if isinstance(event.get("delivery"), dict):
        _delivery(conn, tenant_id, event["delivery"], counts)
    elif isinstance(event.get("read"), dict) and sender:
        _read(conn, tenant_id, ch, sender, event["read"], counts)


def _delivery(
    conn: Conn, tenant_id: uuid.UUID, delivery: Mapping[str, Any], counts: dict[str, int]
) -> None:
    mids = [str(m) for m in delivery.get("mids") or [] if m]
    if not mids:
        return
    conn.execute(
        "UPDATE messages SET status = 'delivered' WHERE tenant_id = %s AND direction = 'out' "
        "AND provider_message_id = ANY(%s) AND status IN ('sent', 'queued', 'sending')",
        (tenant_id, mids),
    )
    counts["statuses"] += 1


def _read(
    conn: Conn,
    tenant_id: uuid.UUID,
    ch: SocialChannel,
    sender: str,
    read: Mapping[str, Any],
    counts: dict[str, int],
) -> None:
    """`read.watermark`: tudo que enviamos a essa pessoa até esse instante (ms) foi lido."""
    try:
        watermark = int(read.get("watermark"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return
    conn.execute(
        "UPDATE messages SET status = 'read' WHERE tenant_id = %s AND direction = 'out' "
        "AND status IN ('sent', 'delivered') AND provider_message_id IS NOT NULL "
        "AND created_at <= to_timestamp(%s / 1000.0) AND conversation_id IN ("
        "SELECT cv.id FROM conversations cv JOIN contact_channels cc "
        "ON cc.contact_id = cv.contact_id AND cc.channel = cv.channel "
        "WHERE cv.tenant_id = %s AND cv.channel = %s AND cc.external_id = %s)",
        (tenant_id, watermark, tenant_id, ch.channel, sender),
    )
    counts["statuses"] += 1
