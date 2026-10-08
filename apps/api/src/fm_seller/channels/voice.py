"""Áudio do cliente: baixa da Meta, transcreve e devolve ao fluxo como texto comum.

Só roda com `FM_VOICE_TRANSCRIPTION=true`. A mensagem entra já "tratada" (o vendedor IA espera);
aqui o texto é trocado pela transcrição e a mensagem volta a "não tratada". O áudio nunca é
guardado nem vai para o log, e a transcrição também não. Se algo falhar (3 tentativas), a mensagem
volta como "[mensagem do tipo audio]", igual ao comportamento de antes, e o vendedor IA decide.
A conferência de pedido de parar (opt-out) acontece depois da transcrição.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

from fm_seller.channels.inbound import apply_opt_out
from fm_seller.channels.meta_api import MetaClient, MetaError
from fm_seller.db import Database
from fm_seller.recovery.optout import is_opt_out
from fm_seller.security.crypto import CryptoError, SecretBox

log = logging.getLogger("fm_seller.voice")

MAX_ATTEMPTS = 3
CLAIM_MINUTES = 2
FALLBACK_BODY = "[mensagem do tipo audio]"
ALLOWED_MIME = ("audio/ogg", "audio/mpeg", "audio/mp4", "audio/aac", "audio/amr", "audio/webm")


class Transcriber(Protocol):
    def transcribe(self, audio: bytes, mime_type: str) -> tuple[str, int, int]: ...


@dataclass
class VoiceStats:
    transcribed: int = 0
    opt_outs: int = 0
    failed: int = 0
    retry: int = 0


def _token(db: Database, box: SecretBox, tenant_id: uuid.UUID) -> str | None:
    with db.tx(system=True) as conn:
        row = conn.execute(
            "SELECT config_encrypted FROM connections WHERE tenant_id = %s "
            "AND provider = 'whatsapp_cloud' ORDER BY created_at LIMIT 1",
            (tenant_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        config = box.decrypt(
            row["config_encrypted"], tenant_id=str(tenant_id), provider="whatsapp_cloud"
        )
    except CryptoError:
        return None
    return str(config.get("access_token", "")) or None


def _finish(
    db: Database, tenant_id: uuid.UUID, msg_id: uuid.UUID, conv_id: uuid.UUID, text: str | None
) -> str:
    """Grava o resultado. `text` None = falha. Devolve 'done', 'opt_out' ou 'failed'."""
    with db.tx(tenant_id=tenant_id) as conn:
        if text is None:
            conn.execute(
                "UPDATE messages SET body = %s, handled = false, media_status = 'failed', "
                "media_id = NULL WHERE id = %s",
                (FALLBACK_BODY, msg_id),
            )
            return "failed"
        if is_opt_out(text):
            conn.execute(
                "UPDATE messages SET body = %s, handled = true, media_status = 'done', "
                "media_id = NULL WHERE id = %s",
                (text, msg_id),
            )
            phone = conn.execute(
                "SELECT ct.phone FROM conversations cv JOIN contacts ct ON ct.id = cv.contact_id "
                "WHERE cv.id = %s",
                (conv_id,),
            ).fetchone()
            if phone and phone["phone"]:
                apply_opt_out(conn, tenant_id, conv_id, phone["phone"])
            return "opt_out"
        conn.execute(
            "UPDATE messages SET body = %s, handled = false, media_status = 'done', "
            "media_id = NULL WHERE id = %s",
            (text, msg_id),
        )
        return "done"


def transcribe_pending(
    db: Database,
    box: SecretBox,
    transcriber: Transcriber,
    meta: MetaClient,
    *,
    max_bytes: int,
    limit: int = 10,
) -> VoiceStats:
    stats = VoiceStats()
    with db.tx(system=True) as conn:
        rows = conn.execute(
            "UPDATE messages SET media_attempts = media_attempts + 1, claimed_at = now() "
            "WHERE id IN (SELECT id FROM messages WHERE media_status = 'pending' "
            "AND media_attempts < %s AND (claimed_at IS NULL OR claimed_at < "
            "now() - make_interval(mins => %s)) ORDER BY created_at LIMIT %s "
            "FOR UPDATE SKIP LOCKED) "
            "RETURNING id, tenant_id, conversation_id, media_id, media_attempts",
            (MAX_ATTEMPTS, CLAIM_MINUTES, limit),
        ).fetchall()
    for row in rows:
        text: str | None = None
        try:
            token = _token(db, box, row["tenant_id"])
            if token and row["media_id"]:
                info = meta.request("GET", str(row["media_id"]), token)
                mime = str(info.get("mime_type", "")).split(";")[0].strip().lower()
                url = str(info.get("url", ""))
                size = info.get("file_size")
                if (
                    mime in ALLOWED_MIME
                    and url
                    and not (isinstance(size, int) and size > max_bytes)
                ):
                    audio = meta.download(url, token, max_bytes=max_bytes)
                    heard, _tin, _tout = transcriber.transcribe(audio, mime)
                    text = heard.strip()[:4000] or None
        except (MetaError, RuntimeError, ValueError) as exc:
            log.warning("áudio não transcrito", extra={"ctx": {"erro": type(exc).__name__}})
        except Exception:
            log.exception("erro inesperado ao transcrever áudio")
        if text is None and row["media_attempts"] < MAX_ATTEMPTS:
            stats.retry += 1  # tenta de novo no próximo ciclo (depois da janela de reserva)
            continue
        outcome = _finish(db, row["tenant_id"], row["id"], row["conversation_id"], text)
        if outcome == "done":
            stats.transcribed += 1
        elif outcome == "opt_out":
            stats.opt_outs += 1
        else:
            stats.failed += 1
    return stats
