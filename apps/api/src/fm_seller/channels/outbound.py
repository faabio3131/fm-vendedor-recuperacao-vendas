"""Fila de saída: entrada de texto na fila, comum a todos os canais."""

from __future__ import annotations

import uuid

from fm_seller.db import Conn

# One canonical transfer notice shared by the seller and the pilot outbox gate.
HANDOFF_MESSAGE = "Certo! Vou chamar uma pessoa da equipe para continuar com você."


def enqueue_text(
    conn: Conn, tenant_id: uuid.UUID, conversation_id: uuid.UUID, author: str, body: str
) -> None:
    conn.execute(
        "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status) "
        "VALUES (%s, %s, 'out', %s, %s, 'queued')",
        (tenant_id, conversation_id, author, body),
    )


def enqueue_photo(
    conn: Conn,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    photo_id: uuid.UUID,
    caption: str,
) -> uuid.UUID:
    """Durable outbound photo: the worker verifies tenant, channel and consent."""
    row = conn.execute(
        "INSERT INTO messages(tenant_id,conversation_id,direction,author,body,"
        "status,outbound_kind,outbound_photo_id) VALUES(%s,%s,'out','bot',%s,"
        "'queued','offer_photo',%s) RETURNING id",
        (tenant_id, conversation_id, caption, photo_id),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row["id"]))
