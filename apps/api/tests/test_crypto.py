from __future__ import annotations

import pytest

from fm_seller.security.crypto import CryptoError, SecretBox


def test_roundtrip_and_no_plaintext_in_blob() -> None:
    box = SecretBox(SecretBox.generate_key_spec())
    blob = box.encrypt(
        {"access_token": "EAAG-super-secreto"}, tenant_id="t1", provider="whatsapp_cloud"
    )
    assert b"EAAG-super-secreto" not in blob
    assert box.decrypt(blob, tenant_id="t1", provider="whatsapp_cloud") == {
        "access_token": "EAAG-super-secreto"
    }


def test_blob_is_bound_to_tenant_and_provider() -> None:
    box = SecretBox(SecretBox.generate_key_spec())
    blob = box.encrypt({"a": "b"}, tenant_id="t1", provider="cakto")
    with pytest.raises(CryptoError):
        box.decrypt(blob, tenant_id="t2", provider="cakto")
    with pytest.raises(CryptoError):
        box.decrypt(blob, tenant_id="t1", provider="hotmart")


def test_tampering_is_detected() -> None:
    box = SecretBox(SecretBox.generate_key_spec())
    blob = bytearray(box.encrypt({"a": "b"}, tenant_id="t1", provider="cakto"))
    blob[-1] ^= 0x01
    with pytest.raises(CryptoError):
        box.decrypt(bytes(blob), tenant_id="t1", provider="cakto")


def test_key_rotation_keeps_old_data_readable() -> None:
    old_spec = SecretBox.generate_key_spec("k1")
    old_box = SecretBox(old_spec)
    blob = old_box.encrypt({"a": "b"}, tenant_id="t1", provider="cakto")
    new_spec = SecretBox.generate_key_spec("k2") + "," + old_spec
    new_box = SecretBox(new_spec)
    assert new_box.decrypt(blob, tenant_id="t1", provider="cakto") == {"a": "b"}
    fresh = new_box.encrypt({"a": "b"}, tenant_id="t1", provider="cakto")
    assert fresh[2:4] == b"k2"
    with pytest.raises(CryptoError):
        old_box.decrypt(fresh, tenant_id="t1", provider="cakto")


@pytest.mark.parametrize("spec", ["", "sem-dois-pontos", "k1:nao-e-base64!!", "k1:YWJj"])
def test_invalid_key_spec_is_rejected(spec: str) -> None:
    with pytest.raises(CryptoError):
        SecretBox(spec)
