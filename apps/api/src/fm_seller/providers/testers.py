"""Testadores de conexão. Nunca declaram sucesso sem verificar de verdade.

- Em dev/teste: `SimulatedTester` confere só se os campos obrigatórios foram preenchidos.
- Em staging/produção, enquanto um adaptador real não existir, o resultado é honesto:
  "não foi possível verificar" e a conexão fica como "precisa de atenção".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fm_seller.config import Settings
from fm_seller.providers.catalog import Provider


@dataclass(frozen=True)
class TestResult:
    __test__ = False  # evita coleta pelo pytest

    ok: bool
    message: str


class ConnectionTester(Protocol):
    def test(self, provider: Provider, config: dict[str, str]) -> TestResult: ...


class SimulatedTester:
    def test(self, provider: Provider, config: dict[str, str]) -> TestResult:
        missing = [f.label for f in provider.fields if f.required and not config.get(f.key)]
        if missing:
            return TestResult(False, "Faltam campos: " + ", ".join(missing))
        return TestResult(True, "Teste simulado: campos preenchidos (ambiente de desenvolvimento).")


class UnavailableTester:
    def test(self, provider: Provider, config: dict[str, str]) -> TestResult:
        return TestResult(
            False,
            f"A verificação automática de {provider.name} ainda não está disponível. "
            "A conexão não foi confirmada.",
        )


class MetaConnectionTester:
    """Teste real do WhatsApp: confere token, número e conta na Graph API (leitura apenas).

    Outros provedores continuam sem verificação automática (resultado honesto: não confirmada).
    """

    def __init__(self, client: object, fallback: ConnectionTester | None = None) -> None:
        from fm_seller.channels.meta_api import MetaClient

        assert isinstance(client, MetaClient)
        self._client = client
        self._fallback = fallback or UnavailableTester()

    def test(self, provider: Provider, config: dict[str, str]) -> TestResult:
        if provider.key != "whatsapp_cloud":
            return self._fallback.test(provider, config)
        from fm_seller.channels.meta_api import MetaRejected, MetaUncertain

        token = config.get("access_token", "")
        number, waba = config.get("phone_number_id", ""), config.get("waba_id", "")
        if not (token and number and waba):
            return TestResult(False, "Faltam o ID do número, o ID da conta ou o token.")
        try:
            phone = self._client.request(
                "GET", number, token, params={"fields": "display_phone_number,verified_name"}
            )
            self._client.request("GET", waba, token, params={"fields": "name"})
        except MetaRejected as exc:
            return TestResult(False, f"A Meta recusou: {exc.detail} (código {exc.code}).")
        except MetaUncertain:
            return TestResult(False, "Não foi possível falar com a Meta agora. Tente de novo.")
        shown = phone.get("display_phone_number") or number
        name = phone.get("verified_name")
        label = f"{shown} ({name})" if name else str(shown)
        return TestResult(True, f"Conectado ao número {label}.")


def build_tester(settings: Settings) -> ConnectionTester:
    base: ConnectionTester = (
        SimulatedTester() if settings.env in ("dev", "test") else UnavailableTester()
    )
    if settings.whatsapp_live and settings.env != "test":
        from fm_seller.channels.meta_api import MetaClient

        return MetaConnectionTester(
            MetaClient(
                base=settings.meta_graph_base,
                version=settings.meta_graph_version,
                timeout=settings.meta_timeout_seconds,
            ),
            fallback=base,
        )
    return base
