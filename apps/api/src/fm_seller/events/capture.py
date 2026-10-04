"""Captura segura de eventos reais de checkout (Cakto e Hotmart).

Objetivo: no dia em que houver conta, ver o que a plataforma REALMENTE manda e comparar com o que o
normalizador espera (`events/compare.py`), sem expor ninguém:

- desligada por padrão (`FM_CAPTURE_EVENTS`); só captura evento que passou na prova de origem;
- segredos (`secret`, `hottok`, tokens, senhas) são redigidos ANTES de guardar; o cabeçalho com o
  hottok nunca é guardado;
- corpo e cabeçalhos ficam cifrados; ao exibir, dados pessoais são mascarados (`mask`);
- expira sozinha (`FM_CAPTURE_TTL_HOURS`) e guarda no máximo `FM_CAPTURE_KEEP` por origem;
- uma falha na captura nunca derruba o recebimento do evento.

`anonymize` gera uma versão com valores sintéticos de mesmo formato, própria para virar fixture de
teste no repositório (nunca dado real).
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fm_seller.db import Conn, Database
from fm_seller.security.crypto import SecretBox

log = logging.getLogger("fm_seller.capture")
REDACTED = "[redigido]"
HIDDEN = "••••"

_SECRET_NAMES = {
    "secret",
    "hottok",
    "token",
    "password",
    "senha",
    "authorization",
    "signature",
    "apikey",
    "api_key",
    "webhook_secret",
}
_SECRET_SUFFIXES = ("token", "secret", "password", "senha")
_NAME_KEYS = {"name", "customername", "fullname", "full_name", "first_name", "last_name", "nome"}
_EMAIL_KEYS = {"email", "customeremail", "checkout_email", "buyer_email"}
_PHONE_KEYS = {
    "phone",
    "cellphone",
    "customercellphone",
    "phone_number",
    "checkout_phone",
    "whatsapp",
    "phone_checkout_number",
    "mobile",
}
_DOC_KEYS = {"docnumber", "doc_number", "doc", "cpf", "cnpj", "document", "document_number"}
_CODE_KEYS = {"qrcode", "qr_code", "pix_qrcode", "copyandpaste", "barcode", "digitableline"}
_ADDRESS_KEYS = {"street", "address", "zipcode", "zip_code", "neighborhood", "complement"}
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DIGITS_RE = re.compile(r"\d{9,}")
_URL_RE = re.compile(r"https?://[^\s\"']+")
SAFE_HEADER_PREFIXES = ("x-cakto-", "x-hotmart-")


@dataclass(frozen=True)
class CaptureConfig:
    enabled: bool = False
    ttl_hours: int = 72
    keep: int = 100


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return k in _SECRET_NAMES or k.endswith(_SECRET_SUFFIXES)


def redact(value: Any) -> Any:
    """Troca por `[redigido]` qualquer chave que pareça segredo, em qualquer profundidade."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if _is_secret_key(str(k)) and v not in (None, "") else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def scrub_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Só cabeçalhos úteis para entender o evento; nunca o que carrega segredo."""
    out: dict[str, str] = {}
    for raw_key, raw_val in headers.items():
        key = raw_key.lower()
        if _is_secret_key(key) or "hottok" in key or key in ("cookie", "authorization"):
            continue
        if key in ("content-type", "user-agent") or key.startswith(SAFE_HEADER_PREFIXES):
            out[key] = str(raw_val)[:300]
    return out


# ----------------------------------------------------------------- máscara e anonimização


def _mask_name(v: str) -> str:
    return " ".join((w[:1] + "•••") if w else w for w in v.split(" "))


def _mask_email(v: str) -> str:
    if "@" not in v:
        return HIDDEN
    user, domain = v.split("@", 1)
    return f"{user[:1]}•••@{domain}"


def _mask_digits(v: str) -> str:
    digits = re.sub(r"\D", "", v)
    return f"••••{digits[-4:]}" if len(digits) >= 4 else HIDDEN


def _mask_url(v: str) -> str:
    """Mantém endereço e caminho; some com o que vem depois do `?` (costuma levar token)."""
    return re.sub(r"\?[^\s\"']*", "?…", v)


def _mask_text(v: str) -> str:
    v = _EMAIL_RE.sub(lambda m: _mask_email(m.group(0)), v)
    v = _URL_RE.sub(lambda m: _mask_url(m.group(0)), v)
    return _DIGITS_RE.sub(lambda m: "•••" + m.group(0)[-2:], v)


def _walk(value: Any, key: str, scalar: Any) -> Any:
    if isinstance(value, dict):
        return {k: _walk(v, str(k).lower(), scalar) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, key, scalar) for v in value]
    return scalar(key, value)


def mask(value: Any) -> Any:
    """Versão para exibir: estrutura e tipos intactos, dados pessoais escondidos."""

    def scalar(key: str, v: Any) -> Any:
        if v is None or isinstance(v, bool | int | float):
            return v
        s = str(v)
        if not s:
            return v
        if key in _NAME_KEYS:
            return _mask_name(s)
        if key in _EMAIL_KEYS:
            return _mask_email(s)
        if key in _PHONE_KEYS:
            return _mask_digits(s)
        if key in _DOC_KEYS or key in _ADDRESS_KEYS:
            return HIDDEN
        if key in _CODE_KEYS or len(s) > 120:
            return "[código ocultado]"
        return _mask_text(s)

    return _walk(redact(value), "", scalar)


def anonymize(value: Any) -> Any:
    """Versão para fixture: valores sintéticos VÁLIDOS (e-mail, telefone, nome), mesmo formato."""
    counter = {"n": 0}

    def scalar(key: str, v: Any) -> Any:
        if v is None or isinstance(v, bool | int | float):
            return v
        s = str(v)
        if not s:
            return v
        counter["n"] += 1
        if key in _NAME_KEYS:
            return "Cliente Exemplo"
        if key in _EMAIL_KEYS or _EMAIL_RE.fullmatch(s):
            return "cliente.exemplo@example.test"
        if key in _PHONE_KEYS:
            return "5511999990001"
        if key in _DOC_KEYS:
            return "00000000000"
        if key in _ADDRESS_KEYS:
            return "Endereço Exemplo"
        if key in _CODE_KEYS or len(s) > 120:
            return "000201-codigo-de-exemplo"
        s = _URL_RE.sub(lambda m: _mask_url(m.group(0)).replace("?…", ""), s)
        return _DIGITS_RE.sub("000000000", s)

    return _walk(redact(value), "", scalar)


# ------------------------------------------------------------------ gravação e consulta


def record(
    conn: Conn,
    box: SecretBox,
    cfg: CaptureConfig,
    *,
    tenant_id: uuid.UUID | None,
    provider: str,
    headers: Mapping[str, str],
    body: dict[str, Any],
    auth_method: str | None,
    now: datetime | None = None,
) -> uuid.UUID | None:
    if not cfg.enabled:
        return None
    stamp = now or datetime.now(UTC)
    scope = str(tenant_id) if tenant_id else "platform"
    row = conn.execute(
        "INSERT INTO event_captures (tenant_id, provider, event_type, auth_method, "
        "headers_encrypted, payload_encrypted, captured_at, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            tenant_id,
            provider,
            (str(body.get("event", "")).strip()[:100] or "unknown"),
            auth_method,
            box.encrypt(scrub_headers(headers), tenant_id=scope, provider=f"capture:{provider}"),
            box.encrypt(redact(body), tenant_id=scope, provider=f"capture:{provider}"),
            stamp,
            stamp + timedelta(hours=cfg.ttl_hours),
        ),
    ).fetchone()
    assert row is not None
    conn.execute(
        "DELETE FROM event_captures WHERE id IN (SELECT id FROM event_captures "
        "WHERE tenant_id IS NOT DISTINCT FROM %s AND provider = %s "
        "ORDER BY captured_at DESC, id OFFSET %s)",
        (tenant_id, provider, cfg.keep),
    )
    return uuid.UUID(str(row["id"]))


def safe_record(
    db: Database,
    box: SecretBox,
    cfg: CaptureConfig,
    *,
    tenant_id: uuid.UUID | None,
    provider: str,
    headers: Mapping[str, str],
    body: dict[str, Any],
    auth_method: str | None,
) -> None:
    """Captura sem nunca atrapalhar o recebimento: erro vira aviso no log, sem dado do evento."""
    if not cfg.enabled:
        return
    try:
        with db.tx(tenant_id=tenant_id, system=tenant_id is None) as conn:
            record(
                conn,
                box,
                cfg,
                tenant_id=tenant_id,
                provider=provider,
                headers=headers,
                body=body,
                auth_method=auth_method,
            )
    except Exception as exc:
        log.warning("captura falhou", extra={"ctx": {"erro": type(exc).__name__}})


def purge_expired(db: Database, *, now: datetime | None = None) -> int:
    with db.tx(system=True) as conn:
        return conn.execute(
            "DELETE FROM event_captures WHERE expires_at <= %s", (now or datetime.now(UTC),)
        ).rowcount


def list_captures(
    conn: Conn, *, limit: int = 50, provider: str | None = None
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, tenant_id, provider, event_type, auth_method, captured_at, expires_at "
        "FROM event_captures WHERE (%s::text IS NULL OR provider = %s) "
        "ORDER BY captured_at DESC LIMIT %s",
        (provider, provider, max(1, min(limit, 200))),
    ).fetchall()
    return [
        {
            "id": str(r["id"]),
            "scope": "plataforma" if r["tenant_id"] is None else "cliente",
            "provider": r["provider"],
            "event_type": r["event_type"],
            "auth_method": r["auth_method"],
            "captured_at": r["captured_at"],
            "expires_at": r["expires_at"],
        }
        for r in rows
    ]


def load_capture(conn: Conn, box: SecretBox, capture_id: uuid.UUID) -> dict[str, Any] | None:
    """Devolve o evento já decifrado (sem segredos). Quem exibe deve usar `mask`."""
    r = conn.execute("SELECT * FROM event_captures WHERE id = %s", (capture_id,)).fetchone()
    if r is None:
        return None
    scope = str(r["tenant_id"]) if r["tenant_id"] else "platform"
    provider = f"capture:{r['provider']}"
    return {
        "id": str(r["id"]),
        "scope": "plataforma" if r["tenant_id"] is None else "cliente",
        "provider": r["provider"],
        "event_type": r["event_type"],
        "auth_method": r["auth_method"],
        "captured_at": r["captured_at"],
        "expires_at": r["expires_at"],
        "headers": box.decrypt(r["headers_encrypted"], tenant_id=scope, provider=provider),
        "payload": box.decrypt(r["payload_encrypted"], tenant_id=scope, provider=provider),
    }
