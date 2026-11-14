"""Canal "web": widget de chat embutido em um site (sem WhatsApp/Messenger/Instagram).

Sem webhook de provedor externo: quem chama é o próprio navegador do visitante. A identidade é
`session_id` gerado pelo widget (guardado no localStorage dele), igual em espírito ao PSID do
Messenger — não é telefone nem e-mail, por isso o contato é `channel_only` (mesma tabela e regras
de Messenger/Instagram, ver migração 0028).
"""

from __future__ import annotations

import re
import uuid

from fm_seller.channels.inbound import record_inbound
from fm_seller.channels.social import identity_of, upsert_social_conversation
from fm_seller.db import Conn

CHANNEL = "web"
MAX_TEXT = 4000
MAX_SESSION_ID = 128
_SESSION_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID.fullmatch(session_id))


def ingest_web_message(
    conn: Conn,
    tenant_id: uuid.UUID,
    *,
    session_id: str,
    text: str,
    message_id: str,
) -> tuple[uuid.UUID, dict[str, int]]:
    """Registra a mensagem do visitante e devolve (conversa, contadores). `message_id` é só
    deste processo (sem retry de provedor): cada POST do widget gera o seu próprio id (um
    `uuid4` do servidor), então duplicidade nesse sentido não se aplica aqui; `record_inbound`
    ainda assim ignora id repetido, pela mesma regra dos outros canais."""
    counts = {"messages": 0, "opt_outs": 0, "duplicates": 0}
    contact_id, conv_id = upsert_social_conversation(conn, tenant_id, CHANNEL, session_id)
    record_inbound(
        conn,
        tenant_id,
        contact_id=contact_id,
        conv_id=conv_id,
        identity=identity_of(CHANNEL, session_id),
        text=text[:MAX_TEXT],
        message_id=message_id,
        counts=counts,
    )
    return conv_id, counts


def latest_reply(conn: Conn, conv_id: uuid.UUID) -> tuple[str, bool] | None:
    """Última mensagem de saída desta conversa (a que a `_handle_conversation` acabou de
    gravar) e se a conversa foi para uma pessoa. `None` só deveria acontecer se nada foi
    produzido (não esperado, mas o chamador decide o que fazer sem o canal explodir)."""
    row = conn.execute(
        "SELECT m.body, cv.status FROM messages m "
        "JOIN conversations cv ON cv.id = m.conversation_id "
        "WHERE m.conversation_id = %s AND m.direction = 'out' "
        "ORDER BY m.created_at DESC LIMIT 1",
        (conv_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row["body"]), row["status"] == "human"
