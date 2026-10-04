"""Fila de saída de texto livre (respostas do vendedor IA, de pessoas e confirmação de saída).

Mesma garantia dos passos de recuperação: no máximo uma vez. A mensagem vira `sending` antes de
falar com o canal; resultado incerto vira `failed`, nunca é reenviado. Texto livre só vale dentro
da janela de 24 h aberta pela última mensagem do cliente (regra de WhatsApp, Messenger e
Instagram; as exceções por etiqueta da Meta não são usadas).

Cada conversa tem um canal. O WhatsApp usa o remetente principal; Messenger e Instagram usam o
remetente do próprio canal (`social`). Sem remetente para o canal, a mensagem NÃO sai: nunca se
manda ID de rede social por um remetente de WhatsApp.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fm_seller.channels.social import BY_CHANNEL, identity_of
from fm_seller.db import Database
from fm_seller.recovery.senders import MessageSender, SendError
from fm_seller.security.crypto import CryptoError, SecretBox

log = logging.getLogger("fm_seller.outbox")
WINDOW = timedelta(hours=24)
STUCK_AFTER = timedelta(hours=1)


@dataclass
class OutboxStats:
    claimed: int = 0
    sent: int = 0
    failed: int = 0
    reaped: int = 0


def flush_outbox(
    db: Database,
    box: SecretBox,
    sender: MessageSender,
    *,
    social: Mapping[str, MessageSender] | None = None,
    now: datetime | None = None,
    limit: int = 50,
) -> OutboxStats:
    now = now or datetime.now(UTC)
    stats = OutboxStats()
    senders: dict[str, MessageSender] = {"whatsapp": sender, **dict(social or {})}
    channels = [name for name, snd in senders.items() if snd.available]
    if not channels:
        return stats
    with db.tx(system=True) as conn:
        stats.reaped = conn.execute(
            "UPDATE messages SET status = 'failed', error = 'resultado_incerto' "
            "WHERE direction = 'out' AND status = 'sending' AND claimed_at < %s",
            (now - STUCK_AFTER,),
        ).rowcount
        rows = conn.execute(
            "UPDATE messages SET status = 'sending', claimed_at = %s WHERE id IN ("
            "SELECT m.id FROM messages m JOIN conversations cv ON cv.id = m.conversation_id "
            "WHERE m.direction = 'out' AND m.status = 'queued' AND cv.channel = ANY(%s) "
            "ORDER BY m.created_at LIMIT %s FOR UPDATE OF m SKIP LOCKED) "
            "RETURNING id, tenant_id, conversation_id, author, body",
            (now, channels, limit),
        ).fetchall()
    for row in rows:
        stats.claimed += 1
        try:
            ok = _send_one(db, box, senders, row, now)
        except Exception:
            log.exception("erro inesperado na fila de saída")
            _finish(db, row["tenant_id"], row["id"], "failed", "erro_interno", None)
            ok = False
        if ok:
            stats.sent += 1
        else:
            stats.failed += 1
    return stats


def _finish(
    db: Database,
    tenant_id: uuid.UUID,
    message_id: uuid.UUID,
    status: str,
    error: str | None,
    provider_id: str | None,
) -> None:
    with db.tx(tenant_id=tenant_id) as conn:
        conn.execute(
            "UPDATE messages SET status = %s, error = %s, provider_message_id = %s WHERE id = %s",
            (status, error, provider_id, message_id),
        )


def _send_one(
    db: Database,
    box: SecretBox,
    senders: Mapping[str, MessageSender],
    row: dict[str, Any],
    now: datetime,
) -> bool:
    tenant_id, message_id = row["tenant_id"], row["id"]
    with db.tx(tenant_id=tenant_id) as conn:
        ctx = conn.execute(
            "SELECT cv.channel, cv.last_inbound_at, ct.phone, cc.external_id, "
            "t.status AS tenant_status FROM conversations cv "
            "JOIN contacts ct ON ct.id = cv.contact_id "
            "LEFT JOIN contact_channels cc ON cc.contact_id = ct.id AND cc.channel = cv.channel "
            "JOIN tenants t ON t.id = cv.tenant_id WHERE cv.id = %s",
            (row["conversation_id"],),
        ).fetchone()
        reason: str | None = None
        config: dict[str, str] = {}
        provider = "whatsapp_cloud"
        recipient = identity = ""
        if ctx is not None:
            channel = ctx["channel"]
            if channel == "whatsapp":
                recipient = identity = ctx["phone"] or ""
            elif channel in BY_CHANNEL and ctx["external_id"]:
                provider = BY_CHANNEL[channel].provider
                recipient = ctx["external_id"]
                identity = identity_of(channel, recipient)
        if ctx is None or not recipient:
            reason = "sem_telefone" if ctx is None or ctx["channel"] == "whatsapp" else "sem_id"
        elif ctx["channel"] not in senders:
            reason = "canal_sem_adaptador"
        elif ctx["tenant_status"] != "active":
            reason = "cliente_suspenso"
        elif ctx["last_inbound_at"] is None or now - ctx["last_inbound_at"] > WINDOW:
            reason = "janela_24h_fechada"
        elif (
            row["author"] != "system"
            and conn.execute(
                "SELECT 1 FROM suppressions WHERE tenant_id = %s AND identity = %s",
                (tenant_id, identity),
            ).fetchone()
        ):
            reason = "nao_contatar"
        if reason is None:
            conn_row = conn.execute(
                "SELECT config_encrypted, status FROM connections "
                "WHERE tenant_id = %s AND provider = %s",
                (tenant_id, provider),
            ).fetchone()
            if conn_row is None or conn_row["status"] != "connected":
                reason = "canal_nao_conectado"
            else:
                try:
                    config = {
                        k: str(v)
                        for k, v in box.decrypt(
                            conn_row["config_encrypted"],
                            tenant_id=str(tenant_id),
                            provider=provider,
                        ).items()
                    }
                except CryptoError:
                    reason = "credencial_ilegivel"
    if reason is not None or ctx is None:
        _finish(db, tenant_id, message_id, "failed", reason, None)
        return False
    try:
        provider_id = senders[ctx["channel"]].send_text(config, recipient, row["body"])
    except SendError as exc:
        _finish(db, tenant_id, message_id, "failed", str(exc)[:200], None)
        return False
    except Exception as exc:
        log.exception("envio de texto com resultado incerto")
        _finish(
            db, tenant_id, message_id, "failed", f"resultado_incerto: {type(exc).__name__}", None
        )
        return False
    _finish(db, tenant_id, message_id, "sent", None, provider_id)
    return True
