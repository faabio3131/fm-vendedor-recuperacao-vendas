"""Porta para criar e consultar templates do WhatsApp na Meta.

Formato conforme a documentação da Meta (POST/GET /<WABA_ID>/message_templates); AINDA NÃO
conferido com uma conta real. O token vai só no cabeçalho.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from fm_seller.channels.meta_api import MetaClient, MetaRejected, MetaUncertain
from fm_seller.config import Settings


class GatewayRejected(Exception):
    """A Meta recusou o pedido (nada foi criado)."""


class GatewayUncertain(Exception):
    """Não foi possível confirmar o resultado (rede, tempo esgotado, resposta estranha)."""


@dataclass(frozen=True)
class RemoteTemplate:
    id: str
    name: str
    language: str
    status: str  # valores da Meta: APPROVED, PENDING, REJECTED, PAUSED, DISABLED...
    category: str | None = None
    reason: str | None = None


class TemplateGateway(Protocol):
    available: bool

    def create(
        self,
        config: dict[str, str],
        *,
        name: str,
        language: str,
        category: str,
        text: str,
        examples: list[str],
    ) -> RemoteTemplate: ...

    def list(self, config: dict[str, str]) -> list[RemoteTemplate]: ...


def _remote(item: dict[str, Any]) -> RemoteTemplate | None:
    name, status = item.get("name"), item.get("status")
    if not isinstance(name, str) or not isinstance(status, str):
        return None
    reason = item.get("rejected_reason")
    return RemoteTemplate(
        id=str(item.get("id", "")),
        name=name,
        language=str(item.get("language", "")),
        status=status.upper(),
        category=item.get("category") if isinstance(item.get("category"), str) else None,
        reason=reason if isinstance(reason, str) and reason.upper() != "NONE" else None,
    )


class MetaTemplateGateway:
    available = True
    MAX_PAGES = 10

    def __init__(self, client: MetaClient) -> None:
        self._client = client

    @staticmethod
    def _creds(config: dict[str, str]) -> tuple[str, str]:
        waba, token = config.get("waba_id", ""), config.get("access_token", "")
        if not waba or not token:
            raise GatewayRejected("conexão do WhatsApp sem ID da conta ou token")
        return waba, token

    def create(
        self,
        config: dict[str, str],
        *,
        name: str,
        language: str,
        category: str,
        text: str,
        examples: list[str],
    ) -> RemoteTemplate:
        waba, token = self._creds(config)
        body: dict[str, Any] = {"type": "BODY", "text": text}
        if examples:
            body["example"] = {"body_text": [examples]}
        payload = {
            "name": name,
            "language": language,
            "category": category,
            "allow_category_change": True,
            "components": [body],
        }
        try:
            data = self._client.request("POST", f"{waba}/message_templates", token, json=payload)
        except MetaRejected as exc:
            raise GatewayRejected(f"{exc.detail} (código {exc.code})") from None
        except MetaUncertain as exc:
            raise GatewayUncertain(str(exc)) from None
        status = data.get("status")
        if not isinstance(status, str):
            raise GatewayUncertain("resposta da Meta sem status")
        return RemoteTemplate(
            id=str(data.get("id", "")),
            name=name,
            language=language,
            status=status.upper(),
            category=data.get("category") if isinstance(data.get("category"), str) else None,
        )

    def list(self, config: dict[str, str]) -> list[RemoteTemplate]:
        waba, token = self._creds(config)
        out: list[RemoteTemplate] = []
        after: str | None = None
        for _ in range(self.MAX_PAGES):
            params: dict[str, Any] = {
                "fields": "name,status,language,category,rejected_reason",
                "limit": 100,
            }
            if after:
                params["after"] = after
            try:
                data = self._client.request(
                    "GET", f"{waba}/message_templates", token, params=params
                )
            except MetaRejected as exc:
                raise GatewayRejected(f"{exc.detail} (código {exc.code})") from None
            except MetaUncertain as exc:
                raise GatewayUncertain(str(exc)) from None
            for item in data.get("data") or []:
                parsed = _remote(item) if isinstance(item, dict) else None
                if parsed:
                    out.append(parsed)
            cursors = (data.get("paging") or {}).get("cursors") or {}
            after = cursors.get("after") if (data.get("paging") or {}).get("next") else None
            if not after:
                break
        return out


@dataclass
class SimulatedTemplateGateway:
    """Só dev/teste. `auto_approve`: PENDING vira APPROVED na consulta seguinte."""

    available: bool = True
    auto_approve: bool = False
    fail_create: str | None = None
    store: dict[tuple[str, str], RemoteTemplate] = field(default_factory=dict)
    created: list[dict[str, Any]] = field(default_factory=list)

    def create(
        self,
        config: dict[str, str],
        *,
        name: str,
        language: str,
        category: str,
        text: str,
        examples: list[str],
    ) -> RemoteTemplate:
        if self.fail_create:
            raise GatewayRejected(self.fail_create)
        item = RemoteTemplate("sim-" + uuid.uuid4().hex[:10], name, language, "PENDING", category)
        self.store[(config.get("waba_id", ""), name)] = item
        self.created.append({"name": name, "text": text, "examples": examples, "cat": category})
        return item

    def set_status(self, waba: str, name: str, status: str, reason: str | None = None) -> None:
        old = self.store[(waba, name)]
        self.store[(waba, name)] = RemoteTemplate(
            old.id, old.name, old.language, status, old.category, reason
        )

    def list(self, config: dict[str, str]) -> list[RemoteTemplate]:
        waba = config.get("waba_id", "")
        if self.auto_approve:
            for (w, n), item in list(self.store.items()):
                if w == waba and item.status == "PENDING":
                    self.set_status(w, n, "APPROVED")
        return [t for (w, _), t in self.store.items() if w == waba]


class UnavailableTemplateGateway:
    available = False

    def create(self, config: dict[str, str], **_: Any) -> RemoteTemplate:
        raise GatewayRejected("O envio de templates para a Meta ainda não está habilitado.")

    def list(self, config: dict[str, str]) -> list[RemoteTemplate]:
        raise GatewayRejected("A consulta de templates na Meta ainda não está habilitada.")


def build_template_gateway(settings: Settings) -> TemplateGateway:
    if settings.whatsapp_live and settings.env != "test":
        return MetaTemplateGateway(
            MetaClient(
                base=settings.meta_graph_base,
                version=settings.meta_graph_version,
                timeout=settings.meta_timeout_seconds,
            )
        )
    if settings.env == "dev":
        return SimulatedTemplateGateway(auto_approve=True)
    if settings.env == "test":
        return SimulatedTemplateGateway()
    return UnavailableTemplateGateway()
