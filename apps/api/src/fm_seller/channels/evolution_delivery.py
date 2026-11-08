"""Read-only, exact-evidence confirmation of an uncertain Evolution send.

Never retries the write operation. The stored message must match the tenant's
verified instance, telephone JID, complete body, direction, and timestamp.
Ambiguity returns None rather than guessing which message was sent.
"""

from __future__ import annotations

import re
import time
from typing import Any

from fm_seller.channels.evolution import EvolutionClient

_INSTANCE = re.compile(r"fmav-[a-f0-9]{32}\Z")
_PHONE = re.compile(r"[0-9]{10,15}\Z")


def find_confirmed_outbound(
    client: EvolutionClient,
    instance: str,
    phone: str,
    text: str,
    *,
    sent_at: float,
    time_tolerance_seconds: int = 60,
) -> str | None:
    """Confirm one and only one matching outbound provider message. No sends."""
    if (
        not _INSTANCE.fullmatch(instance)
        or not _PHONE.fullmatch(phone)
        or not text
        or len(text) > 4000
    ):
        return None
    try:
        payload = client._call(
            "POST",
            f"/chat/findMessages/{instance}",
            body={
                "where": {"key": {"remoteJid": f"{phone}@s.whatsapp.net"}},
                "page": 1,
                "offset": 150,
            },
            max_response=2_000_000,
        )
    except Exception:
        # A failed provider read is not evidence of either success or failure.
        return None
    records: Any = payload.get("messages", payload.get("records"))
    if isinstance(records, dict):
        records = records.get("records")
    if not isinstance(records, list):
        return None
    matched: set[str] = set()
    upper = max(time.time(), sent_at) + 30
    for item in records:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        message = item.get("message")
        if not isinstance(key, dict) or not isinstance(message, dict):
            continue
        if key.get("fromMe") is not True:
            continue
        jid = f"{phone}@s.whatsapp.net"
        if key.get("remoteJid") != jid and key.get("remoteJidAlt") != jid:
            continue
        outbound_text = message.get("conversation")
        extended = message.get("extendedTextMessage")
        if not outbound_text and isinstance(extended, dict):
            outbound_text = extended.get("text")
        if outbound_text != text:
            continue
        raw_timestamp = item.get("messageTimestamp")
        if not isinstance(raw_timestamp, (str, int, float)):
            continue
        try:
            timestamp = float(raw_timestamp)
        except (ValueError, TypeError):
            continue
        if not (sent_at - time_tolerance_seconds <= timestamp <= upper):
            continue
        provider_id = key.get("id")
        if not isinstance(provider_id, str) or not 1 <= len(provider_id) <= 200:
            continue
        matched.add(provider_id)
    return next(iter(matched)) if len(matched) == 1 else None
