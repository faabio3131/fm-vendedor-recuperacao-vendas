"""Envio real de texto pelo Messenger e pelo Instagram (Send API da Meta).

Mesma garantia dos outros canais: no máximo uma vez. Recusa clara da Meta vira `SendError` (não
saiu); rede, tempo esgotado, 5xx ou resposta sem id vira erro comum: o chamador marca "resultado
incerto" e NUNCA reenvia. Só texto livre dentro da janela de 24 h aberta pela pessoa: estes canais
não usam templates. Formato conforme a documentação da Meta; AINDA NÃO conferido com conta real.
"""

from __future__ import annotations

from typing import Any

from fm_seller.channels.meta_api import MetaClient, MetaRejected
from fm_seller.channels.social import SOCIAL, SocialChannel
from fm_seller.config import Settings
from fm_seller.recovery.senders import (
    MessageSender,
    OutboundMessage,
    SendError,
    SimulatedSender,
    UnavailableSender,
)

# Códigos comuns da Graph API/Send API, em português para o painel. Lista curta e não exaustiva:
# conferir com uma conta real. Código desconhecido mostra o texto que a Meta devolveu.
KNOWN_SOCIAL = {
    10: "sem permissão para enviar (confira as permissões do app e do token)",
    100: "pedido inválido",
    190: "token de acesso inválido ou expirado",
    200: "sem permissão para enviar a esta pessoa",
    551: "esta pessoa não está disponível para receber mensagens",
    613: "limite de chamadas da Meta atingido",
}


class SocialSender:
    available = True

    def __init__(self, client: MetaClient, channel: SocialChannel) -> None:
        self._client = client
        self._channel = channel

    def send(self, config: dict[str, str], message: OutboundMessage) -> str:
        raise SendError(f"{self._channel.label} não usa templates.")

    def send_text(self, config: dict[str, str], to_phone: str, body: str) -> str:
        """`to_phone` é o ID da pessoa no canal (a porta de envio é a mesma do WhatsApp)."""
        account = config.get(self._channel.id_field, "")
        token = config.get(self._channel.token_field, "")
        if not account or not token:
            raise SendError(f"conexão do {self._channel.label} sem ID da conta ou token")
        payload: dict[str, Any] = {
            "recipient": {"id": to_phone},
            "messaging_type": "RESPONSE",
            "message": {"text": body},
        }
        try:
            data = self._client.request("POST", f"{account}/messages", token, json=payload)
        except MetaRejected as exc:
            label = KNOWN_SOCIAL.get(exc.code or 0, exc.detail)
            raise SendError(f"meta {exc.code}: {label}"[:200]) from None
        message_id = data.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            # 2xx sem id: a Meta pode ter aceitado. Resultado incerto, nunca reenviar.
            raise RuntimeError("resposta da Meta sem id de mensagem")
        return message_id


def build_social_senders(settings: Settings) -> dict[str, MessageSender]:
    """Um remetente por canal (`conversations.channel`). Real só com o envio real ligado."""
    if settings.whatsapp_live and settings.env != "test":
        client = MetaClient(
            base=settings.meta_graph_base,
            version=settings.meta_graph_version,
            timeout=settings.meta_timeout_seconds,
        )
        return {c.channel: SocialSender(client, c) for c in SOCIAL.values()}
    if settings.env in ("dev", "test"):
        shared = SimulatedSender()
        return {c.channel: shared for c in SOCIAL.values()}
    return {c.channel: UnavailableSender() for c in SOCIAL.values()}
