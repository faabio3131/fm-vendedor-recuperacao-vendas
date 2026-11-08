"""Regras de resposta humana por transporte, independentes de conteúdo do LLM.

24h/templates é requisito da WhatsApp Business *Platform* (Cloud), Messenger e
Instagram. Evolution/Baileys usa sessão WhatsApp Web, não suporta templates Cloud.
Exceção: resposta HUMANA manual a contato que já escreveu no mesmo tenant e
instância; recuperação/bot/campanhas continuam sob janela de 24h e opt-out.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fm_seller.db import Conn

WINDOW = timedelta(hours=24)


def connected_whatsapp_provider(conn: Conn, tenant_id: object) -> str | None:
    """Exigir exatamente um canal WhatsApp conectado, sem escolha arbitrária."""
    rows = conn.execute(
        "SELECT provider FROM connections WHERE tenant_id=%s "
        "AND provider IN ('whatsapp_cloud','evolution') AND status='connected'",
        (tenant_id,),
    ).fetchall()
    return str(rows[0]["provider"]) if len(rows) == 1 else None


def window_open(last_inbound: datetime | None, now: datetime) -> bool:
    return last_inbound is not None and now - last_inbound <= WINDOW


def human_reply_allowed(
    channel: str,
    last_inbound: datetime | None,
    provider: str | None,
    now: datetime,
) -> bool:
    if last_inbound is None:
        return False  # Sem conversa iniciada pelo contato, nunca envio livre.
    if window_open(last_inbound, now):
        return True
    # Não alterar regra Cloud/Meta e jamais habilitar automações fora da janela.
    return channel == "whatsapp" and provider == "evolution"
