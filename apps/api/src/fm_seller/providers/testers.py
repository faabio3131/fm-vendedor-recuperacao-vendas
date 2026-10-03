"""Testadores de conexão. Nunca declaram sucesso sem verificar de verdade.

- Em dev/teste: `SimulatedTester` confere só se os campos obrigatórios foram preenchidos.
- Em staging/produção, enquanto um adaptador real não existir, o resultado é honesto:
  "não foi possível verificar" e a conexão fica como "precisa de atenção".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

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


def build_tester(env: str) -> ConnectionTester:
    return SimulatedTester() if env in ("dev", "test") else UnavailableTester()
