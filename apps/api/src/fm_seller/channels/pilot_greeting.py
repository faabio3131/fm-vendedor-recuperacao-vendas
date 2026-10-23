"""Governed single-contact WhatsApp greeting pilot through the canonical Evolution adapter.

Never enables marketing, recovery, arbitrary text, tenant-wide conversations or other numbers.
The external opt-in is configured in env only, not baked into repository code.
"""

from __future__ import annotations

import hashlib
import re
import uuid

from fm_seller.channels.evolution_runtime import EvolutionSender
from fm_seller.config import Settings
from fm_seller.recovery.senders import OutboundMessage, SendError

GREETINGS = frozenset({"oi", "ola", "bom dia", "boa tarde", "boa noite", "teste anaclara fm"})
UNSAFE_OUTBOUND = re.compile(
    r"https?://|www[.]|r[$]|\b(?:pix|reais|comprar|pagamento|pagar)\b", re.I
)


def pilot_contact(settings: Settings | None, tenant_id: uuid.UUID, phone: str | None) -> bool:
    return bool(
        settings
        and settings.pilot_greeting_enabled
        and str(tenant_id) == settings.pilot_greeting_tenant_id
        and phone == settings.pilot_greeting_phone
    )


def greeting_only(text: str) -> bool:
    from fm_seller.recovery.optout import normalize

    return normalize(text.strip()) in GREETINGS


class PilotGreetingSender(EvolutionSender):
    """Restrict the existing Evolution transport to the sole approved contact/instance."""

    def __init__(self, client: object, *, tenant_id: uuid.UUID, phone: str) -> None:
        super().__init__(client)  # type: ignore[arg-type]
        self.tenant_id = tenant_id
        self.phone = phone
        self.instance = "fmav-" + hashlib.sha256(tenant_id.bytes).hexdigest()[:32]

    def send(self, config: dict[str, str], message: OutboundMessage) -> str:
        raise SendError("piloto não autoriza recuperação nem templates")

    def send_text(self, config: dict[str, str], to_phone: str, body: str) -> str:
        if (
            config.get("__provider") != "evolution"
            or config.get("instance_name") != self.instance
            or to_phone != self.phone
            or not body
            or len(body) > 500
            or UNSAFE_OUTBOUND.search(body)
        ):
            raise SendError("piloto: destinatário, instância ou conteúdo não autorizado")
        return super().send_text(config, to_phone, body)


class PilotCommercialSender(PilotGreetingSender):
    """Authorized *single-offer* sales texts only, never recovery or campaign templates.

    Offer price and checkout URL must be attested by the canonical DB read in
    the outbox for each individual send. No free-form URL/price is authorized.
    """

    commercial_enabled = True

    def __init__(
        self,
        client: object,
        *,
        tenant_id: uuid.UUID,
        phone: str,
        offer_id: uuid.UUID,
        checkout_host: str,
    ) -> None:
        super().__init__(client, tenant_id=tenant_id, phone=phone)
        self.offer_id = offer_id
        self.checkout_host = checkout_host

    def send_text(self, config: dict[str, str], to_phone: str, body: str) -> str:
        from urllib.parse import urlsplit

        url = config.get("__pilot_allowed_url", "")
        price = config.get("__pilot_allowed_price", "")
        parsed = urlsplit(url)
        if (
            config.get("__provider") != "evolution"
            or config.get("instance_name") != self.instance
            or to_phone != self.phone
            or not body
            or len(body) > 1000
            or not url
            or parsed.scheme != "https"
            or parsed.hostname != self.checkout_host
            or not price
            or re.search(r"\b(?:pix|chave|transfer[eê]ncia)\b|www\.", body, re.I)
        ):
            raise SendError("piloto comercial: destinatário, checkout ou conteúdo não autorizado")
        urls = re.findall(r"https?://[^\s<>]+", body, re.I)
        if any(found.rstrip(".,;!?") != url for found in urls):
            raise SendError("piloto comercial: link fora da oferta canônica")
        values = re.findall(r"r\$\s*[0-9][0-9.,]*", body, re.I)
        if any(v.replace(" ", "") != price.replace(" ", "") for v in values):
            raise SendError("piloto comercial: preço fora do cadastro")
        return EvolutionSender.send_text(self, config, to_phone, body)
