"""Fila de saída: entrada de texto na fila, comum a todos os canais."""

from __future__ import annotations

import uuid

from fm_seller.db import Conn


def enqueue_text(
    conn: Conn, tenant_id: uuid.UUID, conversation_id: uuid.UUID, author: str, body: str
) -> None:
    conn.execute(
        "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status) "
        "VALUES (%s, %s, 'out', %s, %s, 'queued')",
        (tenant_id, conversation_id, author, body),
    )
