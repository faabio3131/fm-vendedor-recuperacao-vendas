"""Conversão dos textos do cliente para o formato de template da Meta.

O cliente escreve {nome} {produto} {valor} {link}; a Meta exige variáveis posicionais {{1}}, {{2}}…
e exemplos de preenchimento. No envio, os valores entram como parâmetros na mesma ordem.
Regras de parâmetro conforme a documentação da Meta; AINDA NÃO conferidas com uma conta real:
a Meta pode recusar o template (por exemplo, variável no início ou no fim do texto) e o motivo
volta no status. Parâmetro vazio, com quebra de linha, tabulação ou 5+ espaços seguidos é inválido.
"""

from __future__ import annotations

import re

VARS = ("nome", "produto", "valor", "link")
_VAR = re.compile(r"\{(nome|produto|valor|link)\}")
SAMPLES = {
    "nome": "Maria",
    "produto": "Curso de exemplo",
    "valor": "R$ 97,00",
    "link": "https://exemplo.com.br/pagar",
}
MAX_PARAM = 1024


def to_meta(body: str) -> tuple[str, list[str]]:
    """Texto com {{1}}, {{2}}… e a lista das variáveis, na ordem em que aparecem."""
    names: list[str] = []

    def repl(m: re.Match[str]) -> str:
        names.append(m.group(1))
        return "{{" + str(len(names)) + "}}"

    return _VAR.sub(repl, body), names


def examples(names: list[str]) -> list[str]:
    return [SAMPLES[n] for n in names]


def sanitize_param(value: str) -> str:
    """Parâmetro aceitável pela Meta: sem quebra de linha, tabulação ou 5+ espaços seguidos."""
    return re.sub(r" {2,}", " ", re.sub(r"[\r\n\t]+", " ", value)).strip()[:MAX_PARAM]


def meta_name_for(key: str, version: int) -> str:
    """Nome na Meta. Texto novo = nome novo (a Meta não deixa reusar nome de template existente)."""
    return f"{key}_v{version}"[:512]
