"""Assinatura `X-FMCC-Signature`: HMAC, kid, rotação e proteção contra reenvio."""

from __future__ import annotations

import pytest

from fm_seller.licensing import signature as sig

SECRET = "a" * 40
OTHER = "b" * 40
BODY = b'{"event_id":"evt-1"}'
NOW = 1_800_000_000.0


def header(secret: str = SECRET, kid: str = "k1", t: int = int(NOW), body: bytes = BODY) -> str:
    return sig.sign(secret, body, t=t, kid=kid)


def test_valid_signature_returns_the_kid() -> None:
    assert sig.verify(header(), BODY, {"k1": SECRET}, now=NOW) == "k1"


def test_wrong_secret_tampered_body_and_missing_header_fail() -> None:
    with pytest.raises(sig.SignatureError):
        sig.verify(header(OTHER), BODY, {"k1": SECRET}, now=NOW)
    with pytest.raises(sig.SignatureError):
        sig.verify(header(), BODY + b" ", {"k1": SECRET}, now=NOW)
    for bad in (None, "", "lixo"):
        with pytest.raises(sig.SignatureError):
            sig.verify(bad, BODY, {"k1": SECRET}, now=NOW)


def test_no_configured_secret_never_verifies() -> None:
    with pytest.raises(sig.SignatureError):
        sig.verify(header(), BODY, {}, now=NOW)


def test_replay_window_is_five_minutes_in_both_directions() -> None:
    assert sig.verify(header(t=int(NOW) - 299), BODY, {"k1": SECRET}, now=NOW) == "k1"
    with pytest.raises(sig.SignatureError):
        sig.verify(header(t=int(NOW) - 301), BODY, {"k1": SECRET}, now=NOW)
    with pytest.raises(sig.SignatureError):
        sig.verify(header(t=int(NOW) + 301), BODY, {"k1": SECRET}, now=NOW)


def test_timestamp_is_part_of_the_signed_text() -> None:
    old = header(t=int(NOW) - 10)
    forged = old.replace(f"t={int(NOW) - 10}", f"t={int(NOW)}")
    with pytest.raises(sig.SignatureError):
        sig.verify(forged, BODY, {"k1": SECRET}, now=NOW)


def test_rotation_two_secrets_are_valid_at_the_same_time() -> None:
    keys = {"k1": SECRET, "k2": OTHER}
    assert sig.verify(header(SECRET, "k1"), BODY, keys, now=NOW) == "k1"
    assert sig.verify(header(OTHER, "k2"), BODY, keys, now=NOW) == "k2"
    with pytest.raises(sig.SignatureError):  # segredo de um kid usado com o nome do outro
        sig.verify(header(SECRET, "k2"), BODY, keys, now=NOW)


def test_unknown_kid_fails() -> None:
    with pytest.raises(sig.SignatureError):
        sig.verify(header(kid="k9"), BODY, {"k1": SECRET}, now=NOW)


@pytest.mark.parametrize(
    "bad",
    [
        "t=1,kid=k1",  # sem v1
        f"t={int(NOW)},kid=k1,v1=" + "z" * 64,  # não hexadecimal
        f"t={int(NOW)},t={int(NOW)},kid=k1,v1=" + "a" * 64,  # campo repetido
        "t=abc,kid=k1,v1=" + "a" * 64,
        f"t={int(NOW)},kid=k1,v1=" + "a" * 64 + ",extra=1",  # campo desconhecido
        f"t={int(NOW)},kid=k 1,v1=" + "a" * 64,
    ],
)
def test_malformed_headers_fail(bad: str) -> None:
    with pytest.raises(sig.SignatureError):
        sig.verify(bad, BODY, {"k1": SECRET}, now=NOW)
