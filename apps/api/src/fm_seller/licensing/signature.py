"""Assinatura dos eventos do Command: `X-FMCC-Signature: t=<unix>,kid=<id>,v1=<hex>`.

`v1 = HMAC-SHA256(segredo[kid], "<t>." + corpo bruto)`. Comparação em tempo constante, janela de
5 minutos contra reenvio e rotação por `kid` (dois segredos podem valer ao mesmo tempo).
A resposta de falha é sempre a mesma: quem erra não descobre qual parte falhou.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections.abc import Mapping

TOLERANCE_SECONDS = 300
_KID = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")


class SignatureError(Exception):
    pass


def _parse(header: str) -> tuple[str, str, str]:
    seen: dict[str, str] = {}
    for part in header.split(","):
        key, sep, value = part.strip().partition("=")
        if not sep or key in seen or key not in ("t", "kid", "v1"):
            raise SignatureError
        seen[key] = value
    if set(seen) != {"t", "kid", "v1"}:
        raise SignatureError
    t, kid, sig = seen["t"], seen["kid"], seen["v1"].lower()
    if not t.isdigit() or len(t) > 12 or not _KID.match(kid) or not _HEX.match(sig):
        raise SignatureError
    return t, kid, sig


def sign(secret: str, raw_body: bytes, *, t: int, kid: str) -> str:
    """Monta o cabeçalho (usado pelos testes e pela documentação; o Command tem o par dele)."""
    digest = hmac.new(secret.encode(), f"{t}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return f"t={t},kid={kid},v1={digest}"


def verify(
    header: str | None,
    raw_body: bytes,
    secrets: Mapping[str, str],
    *,
    now: float | None = None,
) -> str:
    """Devolve o `kid` que assinou, ou levanta `SignatureError`."""
    if not header or not secrets:
        raise SignatureError
    t, kid, sig = _parse(header)
    current = time.time() if now is None else now
    if abs(current - int(t)) > TOLERANCE_SECONDS:
        raise SignatureError
    secret = secrets.get(kid)
    if secret is None:
        # Gasta o mesmo trabalho de um kid válido: o tempo não revela quais kid existem.
        hmac.new(b"x", f"{t}.".encode() + raw_body, hashlib.sha256).hexdigest()
        raise SignatureError
    digest = hmac.new(secret.encode(), f"{t}.".encode() + raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig.encode(), digest.encode()):
        raise SignatureError
    return kid
