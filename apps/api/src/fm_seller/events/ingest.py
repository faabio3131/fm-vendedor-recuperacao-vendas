"""Recebimento de webhooks de checkout: verifica, guarda, evita duplicata e repassa ao tratamento.

Princípios: o evento bruto é gravado (cifrado) antes de qualquer decisão; processar duas vezes
o mesmo evento não duplica efeito; falha de processamento não perde o evento nem derruba a
resposta ao remetente (ele fica `failed` para reprocessar).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fm_seller.db import Conn, Database
from fm_seller.errors import AppError, bad_request, not_found
from fm_seller.events.normalize import NORMALIZERS, CheckoutEvent
from fm_seller.security.crypto import CryptoError, SecretBox

log = logging.getLogger("fm_seller.webhooks")
MAX_BODY = 256 * 1024
SECRET_FIELD = {"cakto": "webhook_secret", "hotmart": "hottok"}

# (conn, tenant_id, event) -> None. Levanta exceção se não conseguir tratar.
EventHandler = Callable[[Conn, uuid.UUID, CheckoutEvent], None]


@dataclass(frozen=True)
class IngestResult:
    status: str  # accepted | duplicate | ignored | failed
    event_id: uuid.UUID | None = None


def _candidate_secrets(
    provider: str, headers: Mapping[str, str], body: dict[str, Any]
) -> list[str]:
    """Onde cada plataforma costuma mandar o segredo (conferir com evento real)."""
    h = {k.lower(): v for k, v in headers.items()}
    found: list[str] = []
    if provider == "hotmart":
        found += [h.get("x-hotmart-hottok", ""), str(body.get("hottok", ""))]
    elif provider == "cakto":
        auth = h.get("authorization", "")
        found += [
            str(body.get("secret", "")),
            h.get("x-cakto-secret", ""),
            h.get("x-webhook-secret", ""),
            auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else "",
        ]
    return [c for c in found if c]


def secret_ok(
    provider: str, headers: Mapping[str, str], body: dict[str, Any], expected: str
) -> bool:
    if not expected:
        return False
    ok = False
    for candidate in _candidate_secrets(provider, headers, body):
        ok = hmac.compare_digest(candidate.encode(), expected.encode()) or ok
    return ok


def dedupe_key(body: dict[str, Any], raw: bytes) -> str:
    event_id = body.get("id")
    if isinstance(event_id, str | int) and str(event_id):
        return f"id:{event_id}"
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return "sha:" + hashlib.sha256(canonical).hexdigest()


def ingest_webhook(
    db: Database,
    box: SecretBox,
    *,
    provider: str,
    public_id: str,
    headers: Mapping[str, str],
    raw_body: bytes,
    handler: EventHandler,
) -> IngestResult:
    if provider not in NORMALIZERS:
        raise not_found("Provedor sem recebimento de webhook.")
    if len(raw_body) > MAX_BODY:
        raise AppError(413, "payload_too_large", "Corpo grande demais.")
    try:
        body = json.loads(raw_body)
    except ValueError as exc:
        raise bad_request("invalid_json", "Corpo não é JSON válido.") from exc
    if not isinstance(body, dict):
        raise bad_request("invalid_json", "Corpo precisa ser um objeto JSON.")

    # Achar a conexão pelo identificador público exige olhar fora de um cliente: modo plataforma.
    with db.tx(system=True) as conn:
        row = conn.execute(
            "SELECT c.id, c.tenant_id, c.config_encrypted, t.status AS tenant_status "
            "FROM connections c JOIN tenants t ON t.id = c.tenant_id "
            "WHERE c.public_id = %s AND c.provider = %s",
            (public_id, provider),
        ).fetchone()
    if row is None:
        raise not_found("Conexão não encontrada.")
    tenant_id: uuid.UUID = row["tenant_id"]
    connection_id: uuid.UUID = row["id"]

    try:
        config = box.decrypt(row["config_encrypted"], tenant_id=str(tenant_id), provider=provider)
    except CryptoError:
        log.error("credencial ilegível", extra={"ctx": {"connection_id": str(connection_id)}})
        raise AppError(500, "credentials_unreadable", "Configuração da conexão inválida.") from None
    expected = str(config.get(SECRET_FIELD[provider], ""))
    if not secret_ok(provider, headers, body, expected):
        raise AppError(401, "invalid_secret", "Segredo do webhook inválido.")
    if row["tenant_status"] != "active":
        raise AppError(403, "tenant_suspended", "Cliente suspenso.")

    event_type = str(body.get("event", ""))[:100] or "unknown"
    dedupe = dedupe_key(body, raw_body)
    encrypted = box.encrypt(body, tenant_id=str(tenant_id), provider=f"webhook:{provider}")

    with db.tx(tenant_id=tenant_id) as conn:
        inserted = conn.execute(
            "INSERT INTO webhook_events (tenant_id, connection_id, provider, event_type, "
            "dedupe_key, "
            "payload_encrypted) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (connection_id, dedupe_key) DO NOTHING RETURNING id",
            (tenant_id, connection_id, provider, event_type, dedupe, encrypted),
        ).fetchone()
        # Um evento com segredo válido é prova real de que o vínculo funciona.
        conn.execute(
            "UPDATE connections SET status = 'connected', last_error = NULL, "
            "last_verified_at = now() WHERE id = %s",
            (connection_id,),
        )
    if inserted is None:
        return IngestResult("duplicate")
    event_id: uuid.UUID = inserted["id"]
    return process_stored_event(db, tenant_id, event_id, provider, body, handler)


def process_stored_event(
    db: Database,
    tenant_id: uuid.UUID,
    event_id: uuid.UUID,
    provider: str,
    body: dict[str, Any],
    handler: EventHandler,
) -> IngestResult:
    normalized = NORMALIZERS[provider](body)
    if normalized is None:
        with db.tx(tenant_id=tenant_id) as conn:
            conn.execute(
                "UPDATE webhook_events SET status = 'ignored', processed_at = now() WHERE id = %s",
                (event_id,),
            )
        return IngestResult("ignored", event_id)
    try:
        with db.tx(tenant_id=tenant_id) as conn:
            handler(conn, tenant_id, normalized)
            conn.execute(
                "UPDATE webhook_events SET status = 'processed', processed_at = now() "
                "WHERE id = %s",
                (event_id,),
            )
    except Exception as exc:
        log.exception("falha ao tratar evento", extra={"ctx": {"event_id": str(event_id)}})
        with db.tx(tenant_id=tenant_id) as conn:
            conn.execute(
                "UPDATE webhook_events SET status = 'failed', error = %s, processed_at = now() "
                "WHERE id = %s",
                (type(exc).__name__ + ": " + str(exc)[:300], event_id),
            )
        return IngestResult("failed", event_id)
    return IngestResult("accepted", event_id)


def reprocess_failed(
    db: Database, box: SecretBox, handler: EventHandler, *, limit: int = 100
) -> int:
    """Tenta de novo os eventos que falharam. Devolve quantos foram reprocessados com sucesso."""
    with db.tx(system=True) as conn:
        rows = conn.execute(
            "SELECT id, tenant_id, provider, payload_encrypted FROM webhook_events "
            "WHERE status = 'failed' ORDER BY received_at LIMIT %s",
            (limit,),
        ).fetchall()
    done = 0
    for r in rows:
        body = box.decrypt(
            r["payload_encrypted"],
            tenant_id=str(r["tenant_id"]),
            provider=f"webhook:{r['provider']}",
        )
        result = process_stored_event(db, r["tenant_id"], r["id"], r["provider"], body, handler)
        done += result.status in ("accepted", "ignored")
    return done
