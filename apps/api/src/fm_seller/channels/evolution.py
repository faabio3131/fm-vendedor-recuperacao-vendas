"""Cliente da Evolution API v2 (Baileys): instâncias, QR temporário e estado.

Somente o backend usa a chave da plataforma. Não reutilizar para envio até que
webhooks, roteamento de saída e versão instalada estejam certificados.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any

import httpx

from fm_seller.errors import AppError

_NAME = re.compile(r"fmav-[a-f0-9]{32}\Z")
_PNG = b"\x89PNG\r\n\x1a\n"
_MAX_RESPONSE = 256_000


class InstanceMissing(Exception):
    """A Evolution respondeu 404 para a instância solicitada."""


class EvolutionClient:
    def __init__(self, *, base_url: str, api_key: str, timeout: float = 8.0) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._timeout = timeout

    def _call(
        self, method: str, route: str, *, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self._base or not self._key:
            raise AppError(
                503, "evolution_not_configured", "Pareamento temporariamente indisponível."
            )
        try:
            with httpx.Client(
                timeout=self._timeout, follow_redirects=False, trust_env=False
            ) as client:
                response = client.request(
                    method,
                    f"{self._base}{route}",
                    headers={"apikey": self._key},
                    json=body,
                )
        except httpx.RequestError:
            raise AppError(
                503, "evolution_unreachable", "Evolution indisponível. Tente novamente."
            ) from None
        if response.status_code == 404:
            raise InstanceMissing()
        if response.status_code >= 500:
            raise AppError(503, "evolution_uncertain", "Falha da Evolution; resultado incerto.")
        if response.status_code >= 400:
            raise AppError(502, "evolution_rejected", "A Evolution recusou a operação.") from None
        if len(response.content) > _MAX_RESPONSE:
            raise AppError(
                502, "evolution_response_too_large", "Resposta da Evolution fora do limite."
            )
        try:
            payload: Any = response.json()
        except ValueError:
            raise AppError(
                502, "evolution_invalid_response", "Resposta inválida da Evolution."
            ) from None
        if not isinstance(payload, dict):
            raise AppError(502, "evolution_invalid_response", "Resposta inválida da Evolution.")
        return payload

    @staticmethod
    def _name(name: str) -> str:
        if not _NAME.fullmatch(name):
            raise AppError(400, "invalid_instance", "Instância não autorizada.")
        return name

    def state(self, name: str) -> str:
        safe = self._name(name)
        payload = self._call("GET", f"/instance/connectionState/{safe}")
        instance = payload.get("instance")
        if not isinstance(instance, dict):
            raise AppError(502, "evolution_invalid_state", "Estado da conexão não verificável.")
        reported = instance.get("instanceName")
        if reported != safe:
            raise AppError(502, "evolution_instance_mismatch", "A instância informada não confere.")
        state = instance.get("state")
        if not isinstance(state, str) or state not in ("open", "close", "connecting"):
            raise AppError(502, "evolution_invalid_state", "Estado da conexão não verificável.")
        return state

    def create(self, name: str) -> None:
        safe = self._name(name)
        self._call(
            "POST",
            "/instance/create",
            body={"instanceName": safe, "qrcode": True, "integration": "WHATSAPP-BAILEYS"},
        )

    def configure_webhook(self, name: str, url: str, token: str) -> None:
        """Instance-scoped callback with authenticated header; no global webhooks."""
        safe = self._name(name)
        if not url.startswith("https://") or len(token) < 32:
            raise AppError(503, "webhook_config_invalid", "Configuração do webhook inválida.")
        payload = self._call(
            "POST",
            f"/webhook/set/{safe}",
            body={
                "enabled": True,
                "url": url,
                "webhookByEvents": False,
                "webhookBase64": False,
                "events": ["MESSAGES_UPSERT", "MESSAGES_UPDATE"],
                "headers": {"x-fm-evolution-auth": token},
            },
        )
        # Provider versions differ in wrapper shape. Never accept a clear rejection.
        webhook = payload.get("webhook")
        if isinstance(webhook, dict):
            item = webhook.get("webhook", webhook)
            if isinstance(item, dict) and (
                item.get("enabled") is False
                or (isinstance(item.get("url"), str) and item["url"] != url)
            ):
                raise AppError(502, "webhook_config_mismatch", "Webhook divergente.")

    def qr(self, name: str) -> str:
        safe = self._name(name)
        payload = self._call("GET", f"/instance/connect/{safe}")
        raw = payload.get("base64")
        nested = payload.get("qrcode")
        if not raw and isinstance(nested, dict):
            raw = nested.get("base64")
        if not isinstance(raw, str):
            raise AppError(
                502,
                "evolution_qr_unavailable",
                "A Evolution não retornou uma imagem QR. Confira a versão instalada.",
            )
        encoded = raw.removeprefix("data:image/png;base64,")
        if len(encoded) > _MAX_RESPONSE or not encoded:
            raise AppError(502, "evolution_qr_invalid", "QR inválido ou fora do limite.")
        try:
            image = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise AppError(502, "evolution_qr_invalid", "QR inválido.") from None
        if not image.startswith(_PNG) or len(image) > 160_000:
            raise AppError(502, "evolution_qr_invalid", "Formato de QR não suportado.")
        return "data:image/png;base64," + encoded
