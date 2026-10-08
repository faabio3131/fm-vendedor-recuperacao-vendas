"""Contrato `fmcc.license.v1`: validação pura, espelhando as regras do Command (PR #62)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from fm_seller.licensing.contract import (
    ContractError,
    is_command_id,
    parse_event,
    parse_item,
)
from tests.license_helpers import NOW, Lic, uuid7


def code(body: dict[str, Any]) -> str:
    with pytest.raises(ContractError) as err:
        parse_event(body)
    return err.value.code


def test_valid_event_is_parsed() -> None:
    lic = Lic()
    ev = parse_event(lic.event())
    assert ev.license.state == "active" and ev.license.version == 1
    assert ev.provisioning is not None and ev.provisioning.authorized


def test_test_uuid7_generator_matches_the_contract_rule() -> None:
    assert is_command_id(uuid7())
    assert not is_command_id("123e4567-e89b-42d3-a456-426614174000")  # v4
    assert not is_command_id("nao-e-uuid")
    assert not is_command_id(None)


@pytest.mark.parametrize("field", ["customer_id", "subscription_id"])
def test_ids_must_be_uuidv7_and_email_is_never_a_key(field: str) -> None:
    body = Lic().event()
    body[field] = "cliente@example.test"
    assert code(body) == "invalid_command_id"
    body[field] = "123e4567-e89b-42d3-a456-426614174000"
    assert code(body) == "invalid_command_id"


def test_license_id_must_be_uuidv7() -> None:
    body = Lic().event()
    body["license"]["license_id"] = "123e4567-e89b-42d3-a456-426614174000"
    assert code(body) == "invalid_command_id"


def test_unknown_schema_version_is_refused() -> None:
    body = Lic().event()
    body["schema_version"] = "fmcc.license.v2"
    assert code(body) == "unsupported_schema"


def test_unknown_fields_are_ignored() -> None:
    body = Lic().event()
    body["campo_novo"] = {"x": 1}
    body["license"]["outro"] = 5
    assert parse_event(body).license.version == 1


@pytest.mark.parametrize("version", [0, -1, 1.5, "1", True, None])
def test_version_must_be_a_positive_integer(version: object) -> None:
    body = Lic().event()
    body["license"]["license_version"] = version
    assert code(body) == "invalid_version"


def test_state_must_be_one_of_the_seven() -> None:
    body = Lic().event()
    body["license"]["state"] = "trial"
    assert code(body) == "invalid_state"


@pytest.mark.parametrize(
    ("type_", "state", "ok"),
    [
        ("license.activated", "active", True),
        ("license.renewed", "active", True),
        ("license.past_due", "past_due", True),
        ("license.suspended", "suspended", True),
        ("license.expired", "expired", True),
        ("license.canceled", "canceled", True),
        ("license.refunded", "refunded", True),
        ("license.activated", "suspended", False),
        ("license.suspended", "active", False),
    ],
)
def test_event_type_must_agree_with_the_state(type_: str, state: str, ok: bool) -> None:
    kw: dict[str, Any] = {"state": state}
    if state == "past_due":
        kw["grace_ends_at"] = NOW + timedelta(days=33)
    body = Lic(**kw).event(type_)
    if ok:
        assert parse_event(body).type == type_
    else:
        assert code(body) == "type_state_mismatch"


def test_unknown_event_type_is_refused() -> None:
    body = Lic().event("license.inventado")
    assert code(body) == "invalid_event_type"


def test_validity_must_be_ordered_and_timezone_aware() -> None:
    body = Lic(valid_from=NOW, valid_until=NOW).event()
    assert code(body) == "invalid_validity"
    body = Lic().event()
    body["license"]["valid_until"] = "2026-11-08T00:00:00"  # sem fuso
    assert code(body) == "invalid_validity"


def test_grace_only_in_past_due_and_never_before_the_end_of_validity() -> None:
    assert code(Lic(grace_ends_at=NOW + timedelta(days=40)).event()) == "invalid_grace"  # active
    early = Lic(state="past_due", grace_ends_at=NOW).event("license.past_due")
    assert code(early) == "invalid_grace"
    ok = Lic(state="past_due", grace_ends_at=NOW + timedelta(days=33)).event("license.past_due")
    assert parse_event(ok).license.grace_ends_at is not None


def test_authorized_provisioning_needs_an_admin_email() -> None:
    body = Lic().event()
    body["provisioning"]["admin_email"] = "sem-arroba"
    assert code(body) == "invalid_provisioning"
    body["provisioning"]["authorized"] = "sim"
    assert code(body) == "invalid_provisioning"


def test_event_id_must_be_a_safe_opaque_string() -> None:
    body = Lic().event(event_id="evt com espaço")
    assert code(body) == "invalid_event_id"
    body = Lic().event()
    body["event_id"] = ""
    assert code(body) == "invalid_event_id"


def test_item_for_the_licenses_api_uses_the_same_rules() -> None:
    assert parse_item(Lic().item()).license.version == 1
    bad = Lic().item()
    bad["license"]["state"] = "x"
    with pytest.raises(ContractError):
        parse_item(bad)
    with pytest.raises(ContractError):
        parse_item("texto")
