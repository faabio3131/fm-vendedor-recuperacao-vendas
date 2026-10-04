"""Porta do modelo de IA do vendedor.

O modelo só propõe texto. Ele nunca recebe credenciais e nunca decide preço, desconto, prazo
ou link: isso vem do cadastro de ofertas do cliente e é inserido pelo sistema (ver seller.py).
Em dev/teste há um simulador por regras. Sem adaptador real (staging/produção), a conversa vai
para uma pessoa em vez de a plataforma fingir que a IA respondeu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from fm_seller.config import Settings
from fm_seller.recovery.optout import normalize


@dataclass(frozen=True)
class OfferView:
    id: str
    name: str
    description: str
    price_text: str  # já formatado pelo sistema; o modelo não calcula nem inventa valor


@dataclass(frozen=True)
class AiContext:
    persona: str
    customer_name: str
    offers: tuple[OfferView, ...]
    history: tuple[tuple[str, str], ...]  # (customer|assistant, texto), do mais antigo ao mais novo


@dataclass(frozen=True)
class AiReply:
    """`text` pode usar {oferta}, {preco} e {link}; o sistema preenche a partir de `offer_id`."""

    text: str
    offer_id: str | None = None
    handoff: bool = False
    handoff_reason: str | None = None
    tokens_in: int = 0  # uso informado pelo provedor (medição de custo); 0 se não informado
    tokens_out: int = 0


class AiModel(Protocol):
    available: bool

    def reply(self, ctx: AiContext) -> AiReply: ...


@dataclass
class SimulatedAiModel:
    """Só dev/teste: regras fixas, sem modelo de verdade."""

    available: bool = True
    forced: AiReply | None = None
    calls: list[AiContext] = field(default_factory=list)

    def reply(self, ctx: AiContext) -> AiReply:
        self.calls.append(ctx)
        if self.forced is not None:
            return self.forced
        last = normalize(ctx.history[-1][1]) if ctx.history else ""
        offer = next(
            (o for o in ctx.offers if normalize(o.name) and normalize(o.name) in last),
            ctx.offers[0] if ctx.offers else None,
        )
        if offer is None:
            return AiReply("", handoff=True, handoff_reason="sem_ofertas")
        words = set(last.split())
        if words & {"link", "comprar", "quero", "pagar", "fechar"}:
            return AiReply("Perfeito! Aqui está o link para {oferta}: {link}", offer.id)
        if words & {"preco", "valor", "quanto", "custa"}:
            return AiReply("{oferta} custa {preco}. Quer que eu envie o link?", offer.id)
        return AiReply("Oi! Posso te ajudar com {oferta}? Me conta o que você procura.", offer.id)


class UnavailableAiModel:
    available = False

    def reply(self, ctx: AiContext) -> AiReply:
        raise RuntimeError("Nenhum modelo de IA real está habilitado nesta instalação.")


def build_ai_model(settings: Settings) -> AiModel:
    """Gemini se houver chave (exceto em teste); senão simulador em dev/teste e nada em produção."""
    key = settings.ai_api_key.get_secret_value()
    if key and settings.env != "test":
        from fm_seller.ai.gemini import GeminiModel  # import tardio: evita ciclo com este módulo

        return GeminiModel(
            key,
            model=settings.ai_model,
            base_url=settings.ai_base_url,
            timeout=settings.ai_timeout_seconds,
            thinking_level=settings.ai_thinking_level,
        )
    if settings.env in ("dev", "test"):
        return SimulatedAiModel()
    return UnavailableAiModel()
