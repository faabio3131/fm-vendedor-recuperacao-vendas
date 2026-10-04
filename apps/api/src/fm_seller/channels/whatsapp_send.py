"""Envio real pelo WhatsApp Cloud API (`MessageSender`).

Mesma garantia do motor: no máximo uma vez. Recusa clara da Meta vira `SendError` (não saiu);
rede, tempo esgotado, 5xx ou resposta sem id vira erro comum: o chamador marca "resultado incerto"
e NUNCA reenvia. Não há nova tentativa aqui.
"""

from __future__ import annotations

from typing import Any

from fm_seller.channels.meta_api import MetaClient, MetaRejected
from fm_seller.recovery.senders import OutboundMessage, SendError
from fm_seller.recovery.templates import sanitize_param

# Códigos de erro mais comuns da Meta, em português para o painel (lista não exaustiva).
KNOWN = {
    131047: "janela de 24 h fechada (use um template)",
    131026: "mensagem não pôde ser entregue (número sem WhatsApp ou não aceitou os termos)",
    131048: "limite de spam do número atingido",
    131056: "muitas mensagens para o mesmo contato",
    130429: "limite de envio da Meta atingido",
    132000: "parâmetros do template não conferem",
    132001: "template não existe ou não está aprovado nesse idioma",
    132007: "texto do template viola a política da Meta",
    190: "token de acesso inválido ou expirado",
    100: "pedido inválido",
}


def _reject(exc: MetaRejected) -> SendError:
    label = KNOWN.get(exc.code or 0, exc.detail)
    return SendError(f"meta {exc.code}: {label}"[:200])


class WhatsAppCloudSender:
    available = True

    def __init__(self, client: MetaClient) -> None:
        self._client = client

    def _post(self, config: dict[str, str], body: dict[str, Any]) -> str:
        number = config.get("phone_number_id", "")
        token = config.get("access_token", "")
        if not number or not token:
            raise SendError("conexão do WhatsApp sem número ou token")
        try:
            data = self._client.request("POST", f"{number}/messages", token, json=body)
        except MetaRejected as exc:
            raise _reject(exc) from None
        messages = data.get("messages")
        first = messages[0] if isinstance(messages, list) and messages else None
        message_id = first.get("id") if isinstance(first, dict) else None
        if not isinstance(message_id, str) or not message_id:
            # 2xx sem id: a Meta pode ter aceitado. Resultado incerto, nunca reenviar.
            raise RuntimeError("resposta da Meta sem id de mensagem")
        return message_id

    def send(self, config: dict[str, str], message: OutboundMessage) -> str:
        if not message.meta_name:
            raise SendError("template ainda não aprovado na Meta (sem nome na Meta)")
        params = [sanitize_param(p) for p in message.params]
        if any(not p for p in params):
            raise SendError("parâmetro do template vazio (ex.: contato sem link de pagamento)")
        template: dict[str, Any] = {
            "name": message.meta_name,
            "language": {"code": message.language},
        }
        if params:
            template["components"] = [
                {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}
            ]
        return self._post(
            config,
            {
                "messaging_product": "whatsapp",
                "to": message.to_phone,
                "type": "template",
                "template": template,
            },
        )

    def send_text(self, config: dict[str, str], to_phone: str, body: str) -> str:
        return self._post(
            config,
            {
                "messaging_product": "whatsapp",
                "to": to_phone,
                "type": "text",
                "text": {"body": body, "preview_url": False},
            },
        )
