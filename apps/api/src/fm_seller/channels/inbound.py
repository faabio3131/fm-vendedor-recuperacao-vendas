"""Registro de mensagem recebida, comum a todos os canais.

Cada canal resolve quem escreveu (contato e conversa) e passa a identidade usada no bloqueio de
contato: telefone só com dígitos no WhatsApp, `canal:id` nos canais de rede social.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fm_seller.channels.outbound import enqueue_text
from fm_seller.db import Conn
from fm_seller.recovery.cases import stop_cold_cases
from fm_seller.recovery.optout import is_opt_out, suppress_identity

OPT_OUT_REPLY = (
    "Tudo certo: você não vai mais receber mensagens nossas. Se mudar de ideia, é só nos escrever."
)


def record_inbound(
    conn: Conn,
    tenant_id: uuid.UUID,
    *,
    contact_id: uuid.UUID,
    conv_id: uuid.UUID,
    identity: str,
    text: str,
    message_id: str,
    counts: dict[str, int],
) -> None:
    optout = is_opt_out(text)
    inserted = conn.execute(
        "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status, "
        "provider_message_id, handled) VALUES (%s, %s, 'in', 'customer', %s, 'received', %s, %s) "
        "ON CONFLICT DO NOTHING RETURNING id",
        (tenant_id, conv_id, text, message_id, optout),
    ).fetchone()
    if inserted is None:
        counts["duplicates"] += 1
        return
    counts["messages"] += 1
    suppressed = conn.execute(
        "SELECT 1 FROM suppressions WHERE tenant_id = %s AND identity = %s", (tenant_id, identity)
    ).fetchone()
    if optout:
        suppress_identity(conn, tenant_id, identity, "opt_out")
        conn.execute(
            "UPDATE conversations SET status = 'closed', last_inbound_at = now(), "
            "last_message_at = now() WHERE id = %s",
            (conv_id,),
        )
        enqueue_text(conn, tenant_id, conv_id, "system", OPT_OUT_REPLY)
        counts["opt_outs"] += 1
        return
    stop_cold_cases(conn, tenant_id, contact_id, datetime.now(UTC))
    # Quem escreveu reabre a conversa; o vendedor IA só volta a falar se a pessoa não foi bloqueada.
    conn.execute(
        "UPDATE conversations SET last_inbound_at = now(), last_message_at = now(), "
        "status = CASE WHEN status = 'closed' THEN 'bot' ELSE status END WHERE id = %s",
        (conv_id,),
    )
    if suppressed:
        conn.execute("UPDATE messages SET handled = true WHERE id = %s", (inserted["id"],))
