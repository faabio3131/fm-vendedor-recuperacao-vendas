"""Configuração do Billing Central: tudo desligado por padrão e nada pela metade."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fm_seller.config import Settings, parse_webhook_secrets

S1 = "a" * 40
S2 = "b" * 40
TOKEN = "t" * 40


def cfg(**kw: object) -> Settings:
    return Settings(env="test", **kw)  # type: ignore[arg-type]


def test_everything_is_off_by_default() -> None:
    s = cfg()
    assert s.fmcommand_mode == "off"
    assert s.fmcommand_product_code == "" and s.fmcommand_webhook_secrets == ""
    assert s.fmcommand_api_base_url == "" and s.fmcommand_api_token.get_secret_value() == ""
    assert s.fmcommand_reconcile_minutes == 15 and s.fmcommand_contingency_hours == 72


def test_off_ignores_leftovers_so_it_cannot_block_the_boot() -> None:
    assert (
        cfg(fmcommand_product_code="lixo", fmcommand_api_base_url="http://x").fmcommand_mode
        == "off"
    )


def test_shadow_needs_the_canonical_product_code() -> None:
    with pytest.raises(ValidationError):
        cfg(fmcommand_mode="shadow")
    with pytest.raises(ValidationError):
        cfg(fmcommand_mode="shadow", fmcommand_product_code="atendevendeia")
    assert (
        cfg(fmcommand_mode="shadow", fmcommand_product_code="ATENDEVENDEIA").fmcommand_mode
        == "shadow"
    )


def test_enforce_requires_signature_secrets_and_the_licenses_api() -> None:
    base = {"fmcommand_mode": "enforce", "fmcommand_product_code": "ATENDEVENDEIA"}
    with pytest.raises(ValidationError):
        cfg(**base)
    with pytest.raises(ValidationError):
        cfg(**base, fmcommand_webhook_secrets=f"k1:{S1}")
    ok = cfg(
        **base,
        fmcommand_webhook_secrets=f"k1:{S1}",
        fmcommand_api_base_url="https://command.example.test",
        fmcommand_api_token=TOKEN,
    )
    assert ok.fmcommand_mode == "enforce"


@pytest.mark.parametrize(
    "url",
    ["http://command.example.test", "ftp://x.test", "https://u:p@command.example.test", "https://"],
)
def test_api_base_url_must_be_https_without_credentials(url: str) -> None:
    with pytest.raises(ValidationError):
        cfg(
            fmcommand_mode="shadow",
            fmcommand_product_code="ATENDEVENDEIA",
            fmcommand_api_base_url=url,
            fmcommand_api_token=TOKEN,
        )


def test_api_token_must_be_strong_when_the_api_is_configured() -> None:
    with pytest.raises(ValidationError):
        cfg(
            fmcommand_mode="shadow",
            fmcommand_product_code="ATENDEVENDEIA",
            fmcommand_api_base_url="https://command.example.test",
            fmcommand_api_token="curto",
        )


def test_contingency_has_a_hard_ceiling_of_72_hours() -> None:
    assert cfg(fmcommand_contingency_hours=72).fmcommand_contingency_hours == 72
    for bad in (73, 100, 0):
        with pytest.raises(ValidationError):
            cfg(fmcommand_contingency_hours=bad)


def test_webhook_secrets_parsing_and_rotation() -> None:
    assert parse_webhook_secrets(f"k1:{S1}, k2:{S2}") == {"k1": S1, "k2": S2}
    assert parse_webhook_secrets("") == {}
    for bad in (f"k1:{S1},k1:{S2}", "k1:curto", S1, f"k 1:{S1}", f":{S1}"):
        with pytest.raises(ValueError):
            parse_webhook_secrets(bad)


def test_the_secret_value_never_shows_in_repr() -> None:
    s = cfg(fmcommand_api_token=TOKEN)
    assert TOKEN not in repr(s) and TOKEN not in str(s.model_dump())
