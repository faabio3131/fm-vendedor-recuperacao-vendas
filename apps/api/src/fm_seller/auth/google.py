"""Login com Google (padrão F&M). Verifica o ID token e devolve a identidade.

O verificador é uma porta: em produção valida a assinatura contra as chaves públicas do Google;
em dev/teste existe uma versão simulada que NÃO pode ser usada em staging/produção.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import jwt

from fm_seller.errors import AppError, unauthorized

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    email_verified: bool
    name: str


class GoogleVerifier(Protocol):
    def verify(self, id_token: str) -> GoogleIdentity: ...


KeyResolver = Callable[[str], Any]


class JwksGoogleVerifier:
    """Valida assinatura (RS256), emissor, público (client id) e expiração."""

    def __init__(self, client_id: str, key_resolver: KeyResolver | None = None) -> None:
        if not client_id:
            raise ValueError("FM_GOOGLE_CLIENT_ID é obrigatório para o login com Google")
        self._client_id = client_id
        if key_resolver is None:
            client = jwt.PyJWKClient(GOOGLE_CERTS_URL, cache_keys=True)

            def resolve(token: str) -> Any:
                return client.get_signing_key_from_jwt(token).key

            key_resolver = resolve
        self._resolve = key_resolver

    def verify(self, id_token: str) -> GoogleIdentity:
        try:
            key = self._resolve(id_token)
            claims = jwt.decode(
                id_token,
                key=key,
                algorithms=["RS256"],
                audience=self._client_id,
                options={"require": ["exp", "iss", "sub", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise unauthorized("Não foi possível validar o login com o Google.") from exc
        if claims.get("iss") not in GOOGLE_ISSUERS:
            raise unauthorized("Emissor do login não reconhecido.")
        email = str(claims.get("email", ""))
        if not email:
            raise unauthorized("A conta Google não informou e-mail.")
        return GoogleIdentity(
            sub=str(claims["sub"]),
            email=email,
            email_verified=bool(claims.get("email_verified", False)),
            name=str(claims.get("name", "")),
        )


class SimulatedGoogleVerifier:
    """Somente dev/teste. Token no formato `sim|sub|email|nome|verified(0/1)`."""

    def verify(self, id_token: str) -> GoogleIdentity:
        parts = id_token.split("|")
        if len(parts) != 5 or parts[0] != "sim":
            raise unauthorized("Token de login simulado inválido.")
        return GoogleIdentity(
            sub=parts[1], email=parts[2], name=parts[3], email_verified=parts[4] == "1"
        )


def build_verifier(env: str, client_id: str) -> GoogleVerifier:
    if env in ("dev", "test") and not client_id:
        return SimulatedGoogleVerifier()
    if env in ("staging", "prod") and not client_id:
        raise AppError(500, "config", "FM_GOOGLE_CLIENT_ID não configurado")
    return JwksGoogleVerifier(client_id)
