"""Textos padrão: formato seguro para submeter à Meta e coerência com as sequências."""

from __future__ import annotations

import re

from fm_seller.recovery.defaults import DEFAULT_SEQUENCES, DEFAULT_TEMPLATES
from fm_seller.recovery.templates import to_meta

VAR = re.compile(r"\{[a-z_]+\}")
ALLOWED = {"{nome}", "{produto}", "{valor}", "{link}"}


def test_default_templates_never_start_or_end_with_a_variable() -> None:
    for key, body in DEFAULT_TEMPLATES.items():
        assert not re.match(r"\s*\{", body), f"{key} começa com variável"
        assert not re.search(r"\}\s*$", body), f"{key} termina com variável"


def test_default_templates_use_only_known_variables_and_fit_meta_limits() -> None:
    for key, body in DEFAULT_TEMPLATES.items():
        assert set(VAR.findall(body)) <= ALLOWED, key
        converted, names = to_meta(body)
        assert len(converted) <= 1024, key
        assert len(names) == len(VAR.findall(body)), key
        # Texto mínimo ao redor das variáveis: nunca só variáveis.
        assert len(VAR.sub("", body).strip()) >= 30, key


def test_every_sequence_step_has_a_default_template() -> None:
    keys = {step["template_key"] for steps in DEFAULT_SEQUENCES.values() for step in steps}
    assert keys <= set(DEFAULT_TEMPLATES)
    assert set(DEFAULT_TEMPLATES) <= keys
