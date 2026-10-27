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


def demo_plan_listing_requested(text: str) -> bool:
    """Recognize explicit requests to list plans; the catalogue supplies every commercial fact."""
    from fm_seller.recovery.optout import normalize

    words = set(normalize(text).split())
    return "planos" in words and bool(
        words & {"valores", "valor", "precos", "preco", "custam", "quanto"}
    )


# A single qualified turn is deterministic: it does not ask the model to
# invent a recommendation or burn tokens just to request an essential fact.
DEMO_VOLUME_QUESTION = (
    "Para indicar o plano mais adequado, quantas respostas de IA sua empresa espera usar por mês?"
)


def demo_recommendation_requested(text: str) -> bool:
    """Only an explicit request for plan advice, not arbitrary 'melhor' questions."""
    words = set(__import_normalize(text).split())
    return bool(words & {"plano", "planos"}) and any(
        word.startswith(("recomend", "indic", "suger")) or word in {"melhor", "ideal"}
        for word in words
    )


def __import_normalize(text: str) -> str:
    from fm_seller.recovery.optout import normalize

    return normalize(text)


def demo_requested_volume(text: str, *, direct_answer: bool = False) -> int | None:
    """Parse stated monthly volume, including Brazilian grouping and 'mil'.

    Never interpret a price, decimal or unrelated number as a usage estimate.
    """
    from fm_seller.recovery.optout import normalize

    clean = normalize(text)
    digit_count = r"(?:[1-9][0-9]{0,5}|[1-9][0-9]{0,2}(?:\.[0-9]{3})+)"
    count_token = rf"(?:{digit_count}|[1-9][0-9]{{0,2}}\s+mil)"

    def parse(candidate: str) -> int | None:
        if not re.fullmatch(count_token, candidate, re.IGNORECASE):
            return None
        if candidate.lower().endswith(" mil"):
            amount = int(candidate[:-4]) * 1000
        else:
            amount = int(candidate.replace(".", ""))
        return amount if 1 <= amount <= 999_999 else None

    if direct_answer:
        answer = parse(text.strip())
        if answer is not None:
            return answer
    if not direct_answer and not (
        ("por mes" in clean or "mensais" in clean)
        and set(clean.split())
        & {"respostas", "mensagens", "mensagem", "msgs", "msg", "atendimentos"}
    ):
        return None
    match = re.search(
        rf"(?<![0-9.,])({count_token})\s+(?:respostas|mensagens|mensagem|msgs?|atendimentos)\b",
        text.lower(),
    )
    return parse(match.group(1)) if match else None


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
            or re.search(
                r"\b(?:chave|copia e cola|pagamento confirmado|assinatura ativada)\b|"
                r"\bpix\s+(?:copia|chave|qr)|\b(?:chave|qr)\s*(?:code\s*)?pix\b",
                body,
                re.I,
            )
        ):
            raise SendError("piloto demo: destino ou conteúdo não autorizado")
        prices = set(config.get("__demo_prices", "").split("|")) - {""}
        quotas = set(config.get("__demo_quotas", "").split("|")) - {""}
        if not prices or not quotas:
            raise SendError("piloto demo: catálogo comercial indisponível")
        urls = [url.rstrip(".,;!?") for url in re.findall(r"https?://[^\s<>]+", body, re.I)]
        links = set(config.get("__demo_links", "").split("|")) - {""}
        if any(url != DEMO_PAGE and url not in links for url in urls):
            raise SendError("piloto demo: URL fora da landing oficial")
        without_urls = body
        for url in urls:
            without_urls = without_urls.replace(url, "")
        found_prices = re.findall(r"R\$\s*[0-9][0-9.]*,[0-9]{2}", without_urls, re.I)
        approved_prices = {price.replace(" ", "") for price in prices}
        if any(value.replace(" ", "") not in approved_prices for value in found_prices):
            raise SendError("piloto demo: preço divergente do catálogo")
        for price in found_prices:
            without_urls = without_urls.replace(price, "")
        numbers = re.findall(r"(?<!\w)[0-9][0-9.]*", without_urls)
        echoed = set(config.get("__demo_echo", "").split("|")) - {""}
        if any(number not in quotas and number.rstrip(".") not in echoed for number in numbers):
            raise SendError("piloto demo: número não autorizado")
        return EvolutionSender.send_text(self, config, to_phone, body)
