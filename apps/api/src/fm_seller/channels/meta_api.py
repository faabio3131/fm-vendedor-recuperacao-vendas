"""Cliente mínimo da Graph API da Meta (WhatsApp Cloud API).

Formato conforme a documentação da Meta; AINDA NÃO conferido com uma conta real. O token de acesso
vai só no cabeçalho Authorization e nunca em log, URL ou mensagem de erro.

Resultado de uma chamada que altera algo (envio, criação de template):
- resposta 2xx → sucesso;
- recusa clara da Meta (4xx com erro no corpo) → `MetaRejected`: nada foi feito;
- rede, tempo esgotado ou 5xx → `MetaUncertain`: pode ter sido feito. Quem envia mensagem nunca
  repete nesse caso (no máximo uma vez).
"""

from __future__ import annotations

from typing import Any

import httpx


class MetaError(Exception):
    pass


class MetaRejected(MetaError):
    """A Meta recusou o pedido: nada foi feito."""

    def __init__(self, status: int, code: int | None, message: str) -> None:
        super().__init__(f"Meta {status}/{code}: {message}")
        self.status = status
        self.code = code
        self.detail = message


class MetaUncertain(MetaError):
    """Não dá para saber se o pedido foi executado (rede, tempo esgotado, 5xx)."""


class MetaClient:
    def __init__(
        self,
        *,
        base: str = "https://graph.facebook.com",
        version: str = "v26.0",
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._root = f"{base.rstrip('/')}/{version}"
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            res = self._client.request(
                method,
                f"{self._root}/{path.lstrip('/')}",
                json=json,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise MetaUncertain(f"rede: {type(exc).__name__}") from None
        if res.status_code >= 500:
            raise MetaUncertain(f"http {res.status_code}")
        try:
            data = res.json()
        except ValueError:
            data = None
        if res.status_code >= 400:
            err = data.get("error") if isinstance(data, dict) else None
            err = err if isinstance(err, dict) else {}
            code = err.get("code")
            raise MetaRejected(
                res.status_code,
                code if isinstance(code, int) else None,
                str(err.get("message") or err.get("error_user_msg") or "sem detalhe")[:150],
            )
        if not isinstance(data, dict):
            raise MetaUncertain("resposta fora do formato")
        return data
