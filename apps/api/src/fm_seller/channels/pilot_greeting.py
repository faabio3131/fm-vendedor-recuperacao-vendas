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


DEMO_PAGE = "https://fmtecnologiaia.com.br/produtos/atendevendeia"


class PilotDemoSender(PilotGreetingSender):
    """Same one-contact Evolution transport; only current DB-approved plan claims."""

    demo_enabled = True

    def send_text(self, config: dict[str, str], to_phone: str, body: str) -> str:
        if (
            config.get("__provider") != "evolution"
            or config.get("instance_name") != self.instance
            or to_phone != self.phone
            or not body
            or len(body) > 850
            or re.search(r"\\b(?:pix|chave|pagamento confirmado|assinatura ativada)\\b", body, re.I)
        ):
            raise SendError("piloto demo: destino ou conteúdo não autorizado")
        prices = set(config.get("__demo_prices", "").split("|")) - {""}
        quotas = set(config.get("__demo_quotas", "").split("|")) - {""}
        if not prices or not quotas:
            raise SendError("piloto demo: catálogo comercial indisponível")
        urls = [url.rstrip(".,;!?") for url in re.findall(r"https?://[^\\s<>]+", body, re.I)]
        if any(url != DEMO_PAGE for url in urls):
            raise SendError("piloto demo: URL fora da landing oficial")
        without_urls = body
        for url in urls:
            without_urls = without_urls.replace(url, "")
        found_prices = re.findall(r"R\\$\\s*[0-9][0-9.]*,[0-9]{2}", without_urls, re.I)
        if any(price.replace(" ", "") not in {p.replace(" ", "") for p in prices} for price in found_prices):
            raise SendError("piloto demo: preço divergente do catálogo")
        for price in found_prices:
            without_urls = without_urls.replace(price, "")
        numbers = re.findall(r"(?<!\\w)[0-9][0-9.]*", without_urls)
        if any(number not in quotas for number in numbers):
            raise SendError("piloto demo: número não autorizado")
        return EvolutionSender.send_text(self, config, to_phone, body)
