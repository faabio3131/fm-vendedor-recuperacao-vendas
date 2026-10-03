"""Porta de envio de mensagens. Nunca finge sucesso fora de dev/teste."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from fm_seller.config import Settings


class SendError(Exception):
    """Envio recusado de forma definitiva (a mensagem NÃO foi entregue ao canal)."""


@dataclass(frozen=True)
class OutboundMessage:
    tenant_id: uuid.UUID
    to_phone: str  # só dígitos, com DDI
    template_key: str
    body: str
    # Envio real: nome do template aprovado na Meta, idioma e valores das variáveis, em ordem.
    meta_name: str | None = None
    language: str = "pt_BR"
    params: tuple[str, ...] = ()


class MessageSender(Protocol):
    available: bool

    def send(self, config: dict[str, str], message: OutboundMessage) -> str:
        """Envia e devolve o id da mensagem no provedor. Levanta SendError se recusar."""
        ...

    def send_text(self, config: dict[str, str], to_phone: str, body: str) -> str:
        """Texto livre, só válido dentro da janela de 24 h aberta pelo cliente."""
        ...


@dataclass
class SimulatedSender:
    """Só dev/teste: guarda o que "enviaria". Não fala com nenhum provedor."""

    available: bool = True
    sent: list[OutboundMessage] = field(default_factory=list)
    texts: list[tuple[str, str]] = field(default_factory=list)
    fail_with: str | None = None

    def send(self, config: dict[str, str], message: OutboundMessage) -> str:
        if self.fail_with:
            raise SendError(self.fail_with)
        self.sent.append(message)
        return "sim-" + uuid.uuid4().hex[:12]

    def send_text(self, config: dict[str, str], to_phone: str, body: str) -> str:
        if self.fail_with:
            raise SendError(self.fail_with)
        self.texts.append((to_phone, body))
        return "sim-" + uuid.uuid4().hex[:12]


class UnavailableSender:
    """Staging/produção enquanto o envio real não estiver ligado (FM_WHATSAPP_LIVE), o que só deve
    acontecer depois de validar com uma conta verificada na Meta. O worker não pega passos."""

    available = False

    def send(self, config: dict[str, str], message: OutboundMessage) -> str:
        raise SendError("Envio real pelo WhatsApp ainda não está habilitado nesta instalação.")

    def send_text(self, config: dict[str, str], to_phone: str, body: str) -> str:
        raise SendError("Envio real pelo WhatsApp ainda não está habilitado nesta instalação.")


def build_sender(settings: Settings) -> MessageSender:
    """Real só com FM_WHATSAPP_LIVE ligado (e nunca em teste); senão simulador em dev/teste."""
    if settings.whatsapp_live and settings.env != "test":
        from fm_seller.channels.meta_api import MetaClient
        from fm_seller.channels.whatsapp_send import WhatsAppCloudSender

        return WhatsAppCloudSender(
            MetaClient(
                base=settings.meta_graph_base,
                version=settings.meta_graph_version,
                timeout=settings.meta_timeout_seconds,
            )
        )
    if settings.env in ("dev", "test"):
        return SimulatedSender()
    return UnavailableSender()
