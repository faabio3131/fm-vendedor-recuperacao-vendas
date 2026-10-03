from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from fm_seller.auth.google import JwksGoogleVerifier
from fm_seller.errors import AppError

CLIENT_ID = "cliente-teste.apps.googleusercontent.com"


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


SIGNING = _key()
OTHER = _key()


def _token(key: rsa.RSAPrivateKey = SIGNING, **over: Any) -> str:
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1234567890",
        "email": "pessoa@example.test",
        "email_verified": True,
        "name": "Pessoa",
        "exp": int(time.time()) + 600,
    }
    claims.update(over)
    return jwt.encode(claims, key, algorithm="RS256")


def _verifier() -> JwksGoogleVerifier:
    return JwksGoogleVerifier(CLIENT_ID, key_resolver=lambda _t: SIGNING.public_key())


def test_valid_token_is_accepted() -> None:
    identity = _verifier().verify(_token())
    assert identity.email == "pessoa@example.test"
    assert identity.email_verified is True


@pytest.mark.parametrize(
    "token",
    [
        _token(aud="outro-app"),
        _token(iss="https://evil.example"),
        _token(exp=int(time.time()) - 10),
        _token(key=OTHER),
        "isso.nao.e-jwt",
    ],
    ids=["audience", "issuer", "expired", "bad-signature", "garbage"],
)
def test_invalid_tokens_are_rejected(token: str) -> None:
    with pytest.raises(AppError) as err:
        _verifier().verify(token)
    assert err.value.status == 401


def test_client_id_is_required() -> None:
    with pytest.raises(ValueError):
        JwksGoogleVerifier("")
