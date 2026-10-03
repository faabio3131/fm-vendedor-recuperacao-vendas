"""Formatação de valores."""

from __future__ import annotations


def format_brl(cents: int) -> str:
    reais, rest = divmod(cents, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{rest:02d}"
