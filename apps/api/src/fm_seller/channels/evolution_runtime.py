"""Evolution v2: authenticated per-instance inbound and deterministic outbound.

No retry after an ambiguous send, no alternate provider fallback, no client-supplied tenancy.
Webhook auth is a per-instance bearer derived from an independent platform secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from fm_seller.channels.evolution import EvolutionClient
from fm_seller.channels.inbound import record_inbound
from fm_seller.channels.outbound import enqueue_text
from fm_seller.channels.voice import AUDIO_UNAVAILABLE_MESSAGE
from fm_seller.channels.whatsapp import AUDIO_PLACEHOLDER, load_connection, upsert_conversation
from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.errors import AppError
from fm_seller.events.normalize import normalize_phone_br
from fm_seller.recovery.senders import MessageSender, OutboundMessage, SendError
from fm_seller.security.crypto import SecretBox

MAX_WEBHOOK = 256_000
JID = re.compile(r"^([0-9]{10,15})@s\.whatsapp\.net$")
PROVIDER = "evolution"
STATUS = {
    "PENDING": "sent",
    "SERVER_ACK": "sent",
    "DELIVERY_ACK": "delivered",
    "READ": "read",
    "PLAYED": "read",
    "ERROR": "failed",
}
_ORDER = ("sent", "delivered", "read")


def _audio_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """Recognize voice notes including ephemeral wrappers, not arbitrary attachments."""
    candidate: Any = message
    for _ in range(4):
        if not isinstance(candidate, dict):
            return None
        audio = candidate.get("audioMessage")
        if isinstance(audio, dict):
            return audio
        wrapper = next(
            (
                candidate[k]
                for k in ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2")
                if isinstance(candidate.get(k), dict)
            ),
            None,
        )
        if wrapper is None:
            return None
        candidate = wrapper.get("message")
    return None


def webhook_token(settings: Settings, tenant_id: uuid.UUID, instance: str) -> str:
    master = settings.evolution_webhook_key.get_secret_value()
    if not settings.evolution_enabled or len(master) < 32:
        raise AppError(503, "evolution_webhook_disabled", "Webhook não configurado.")
    payload = b"atendevendeia-evolution-v1:" + tenant_id.bytes + instance.encode("ascii")
    return hmac.new(master.encode(), payload, hashlib.sha256).hexdigest()


def ingest_evolution(
    db: Database,
    box: SecretBox,
    settings: Settings,
    *,
    public_id: str,
    headers: Mapping[str, str],
    raw_body: bytes,
) -> dict[str, int]:
    if not settings.evolution_enabled:
        raise AppError(503, "evolution_disabled", "Canal não habilitado.")
    if len(raw_body) > MAX_WEBHOOK:
        raise AppError(413, "payload_too_large", "Evento excede o limite.")
    connection_id, tenant_id, config, tenant_status = load_connection(db, box, public_id, PROVIDER)
    name = str(config.get("instance_name", ""))
    # Forbid arbitrary instance names even if the encrypted row was corrupted/migrated.
    expected_name = "fmav-" + hashlib.sha256(tenant_id.bytes).hexdigest()[:32]
    if name != expected_name or public_id != name:
        raise AppError(403, "instance_mismatch", "Instância não autorizada.")
    token = webhook_token(settings, tenant_id, name)
    supplied = headers.get("x-fm-evolution-auth", "")
    if not isinstance(supplied, str) or not hmac.compare_digest(token, supplied):
        raise AppError(401, "invalid_webhook_auth", "Webhook sem autenticação válida.")
    if tenant_status != "active":
        raise AppError(403, "tenant_suspended", "Cliente suspenso.")
    try:
        body: Any = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        raise AppError(400, "invalid_json", "Corpo de webhook inválido.") from None
    if not isinstance(body, dict) or body.get("instance") != name:
        raise AppError(403, "instance_mismatch", "Instância do evento não confere.")
    event = body.get("event")
    counts = {"messages": 0, "statuses": 0, "opt_outs": 0, "duplicates": 0}
    if event not in ("messages.upsert", "messages.update"):
        return counts  # Do not trust webhook connection.update as connection proof.
    data = body.get("data")
    if not isinstance(data, dict):
        raise AppError(400, "invalid_event", "Dados do evento inválidos.")
    with db.tx(tenant_id=tenant_id) as conn:
        linked = conn.execute(
            "SELECT status FROM connections WHERE tenant_id = %s AND id = %s",
            (tenant_id, connection_id),
        ).fetchone()
        if linked is None or linked["status"] != "connected":
            raise AppError(409, "evolution_not_verified", "Sessão não verificada.")
        if event == "messages.upsert":
            key = data.get("key")
            msg = data.get("message")
            if not isinstance(key, dict) or not isinstance(msg, dict):
                raise AppError(400, "invalid_message", "Mensagem inválida.")
            if key.get("fromMe") is not False:
                return counts  # Never reply to our own messages.
            jid = str(key.get("remoteJid", ""))
            match = JID.fullmatch(jid)
            message_id = str(key.get("id", ""))
            if not match or not 1 <= len(message_id) <= 200:
                return counts  # Reject groups, LIDs, broadcast and bad IDs.
            phone = normalize_phone_br(match.group(1))
            if not phone:
                return counts
            text = msg.get("conversation") or (msg.get("extendedTextMessage") or {}).get("text")
            audio = _audio_message(msg)
            if (not isinstance(text, str) or not text.strip()) and audio is None:
                return counts  # Unsupported media cannot fabricate a text reply.
            contact_id, conv_id = upsert_conversation(
                conn, tenant_id, phone, str(data.get("pushName", ""))[:200]
            )
            audio_enabled = bool(
                audio is not None
                and settings.voice_transcription
                and (
                    not settings.pilot_greeting_enabled
                    or (
                        str(tenant_id) == settings.pilot_greeting_tenant_id
                        and phone == settings.pilot_greeting_phone
                    )
                )
            )
            previous_messages = counts["messages"]
            record_inbound(
                conn,
                tenant_id,
                contact_id=contact_id,
                conv_id=conv_id,
                identity=phone,
                text=(
                    AUDIO_PLACEHOLDER
                    if audio_enabled
                    else "[áudio recebido; transcrição desativada]"
                    if audio is not None
                    else str(text)[:4000]
                ),
                message_id="evo:" + message_id,
                counts=counts,
                media_id="evo:" + message_id if audio_enabled else None,
            )
            if audio is not None and not audio_enabled:
                # Still show the real inbound in the operator inbox, without
                # asking the model to hallucinate what an untranscribed audio said.
                conn.execute(
                    "UPDATE messages SET handled = true, media_status = 'failed' "
                    "WHERE tenant_id = %s AND provider_message_id = %s AND direction = 'in'",
                    (tenant_id, "evo:" + message_id),
                )
                # Only the exact authorized pilot contact gets a text-only notice.
                # Replay of the same webhook must never enqueue a second notice.
                if (
                    counts["messages"] > previous_messages
                    and settings.pilot_greeting_enabled
                    and str(tenant_id) == settings.pilot_greeting_tenant_id
                    and phone == settings.pilot_greeting_phone
                ):
                    conversation_state = conn.execute(
                        "SELECT status FROM conversations WHERE id = %s", (conv_id,)
                    ).fetchone()
                    suppressed = conn.execute(
                        "SELECT 1 FROM suppressions WHERE tenant_id = %s AND identity = %s",
                        (tenant_id, phone),
                    ).fetchone()
                    if (
                        conversation_state is not None
                        and conversation_state["status"] == "bot"
                        and not suppressed
                    ):
                        enqueue_text(conn, tenant_id, conv_id, "bot", AUDIO_UNAVAILABLE_MESSAGE)
        else:
            key = data.get("key")
            identifier = (key or {}).get("id") if isinstance(key, dict) else None
            identifier = identifier or data.get("keyId")
            status = STATUS.get(str(data.get("status", "")).upper())
            if not isinstance(identifier, str) or not status:
                return counts
            # Updates are tenant-scoped and monotonic. A replay cannot downgrade read to sent.
            conn.execute(
                "UPDATE messages SET status = %s WHERE tenant_id = %s AND "
                "provider_message_id = %s AND direction = 'out' "
                "AND status = ANY(%s) AND "
                "array_position(%s::text[], status) < array_position(%s::text[], %s)",
                (status, tenant_id, identifier, list(_ORDER), list(_ORDER), list(_ORDER), status),
            )
            conn.execute(
                "UPDATE recovery_steps SET delivery_status = %s WHERE tenant_id = %s "
                "AND provider_message_id = %s AND "
                "coalesce(array_position(%s::text[], delivery_status), 0) < "
                "array_position(%s::text[], %s)",
                (status, tenant_id, identifier, list(_ORDER), list(_ORDER), status),
            )
            counts["statuses"] += 1
    return counts


class EvolutionSender:
    """MessageSender adapter sharing the canonical worker and existing outbox."""

    available = True

    def __init__(self, client: EvolutionClient) -> None:
        self.client = client

    def send_text(self, config: dict[str, str], to_phone: str, body: str) -> str:
        instance = config.get("instance_name", "")
        if not re.fullmatch(r"fmav-[a-f0-9]{32}", instance):
            raise SendError("instância Evolution não vinculada")
        if not re.fullmatch(r"[0-9]{10,15}", to_phone) or not body or len(body) > 4000:
            raise SendError("mensagem ou destinatário inválido")
        try:
            state = self.client.state(instance)
        except AppError:
            raise SendError("sessão Evolution não verificada") from None
        if state != "open":
            raise SendError("sessão Evolution desconectada")
        try:
            response = self.client._call(
                "POST",
                f"/message/sendText/{instance}",
                body={"number": to_phone, "text": body},
            )
        except AppError as exc:
            # HTTP/network uncertainty is NOT a definite rejection.
            if exc.code == "evolution_rejected":
                raise SendError("Evolution recusou envio") from None
            raise RuntimeError("resultado do envio Evolution incerto") from None
        key = response.get("key")
        message_id = key.get("id") if isinstance(key, dict) else None
        if not isinstance(message_id, str) or not 1 <= len(message_id) <= 200:
            raise RuntimeError("Evolution não confirmou ID do envio")
        return message_id

    def send(self, config: dict[str, str], message: OutboundMessage) -> str:
        return self.send_text(config, message.to_phone, message.body)


class RoutedWhatsAppSender:
    """No implicit Meta fallback: provider comes only from the DB-selected connection."""

    def __init__(
        self,
        meta: MessageSender,
        evolution: MessageSender,
        *,
        meta_enabled: bool,
        evolution_enabled: bool,
        pilot_tenant_id: uuid.UUID | None = None,
        pilot_phone: str = "",
    ) -> None:
        self.pilot_only = pilot_tenant_id is not None
        self.pilot_tenant_id = pilot_tenant_id
        self.pilot_phone = pilot_phone
        self.meta = meta
        self.evolution = evolution
        self.meta_enabled = meta_enabled
        self.evolution_enabled = evolution_enabled
        self.available = meta_enabled or evolution_enabled

    def _selected(self, config: dict[str, str]) -> MessageSender:
        provider = config.get("__provider", "whatsapp_cloud")
        if provider == "evolution" and self.evolution_enabled:
            return self.evolution
        if provider == "whatsapp_cloud" and self.meta_enabled:
            return self.meta
        raise SendError("provedor WhatsApp não habilitado")

    def send(self, config: dict[str, str], message: OutboundMessage) -> str:
        return self._selected(config).send(config, message)

    def send_text(self, config: dict[str, str], to_phone: str, body: str) -> str:
        return self._selected(config).send_text(config, to_phone, body)
