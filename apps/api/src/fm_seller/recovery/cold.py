"""Conversa que esfriou: o cliente perguntou, foi respondido (vendedor IA ou pessoa) e sumiu.

O worker procura essas conversas e abre uma oportunidade de recuperação para cada uma. Só roda
para quem ligou a recuperação, declarou o consentimento e ligou esta detecção em Ajustes.
Não depende de integração nenhuma: o dado já está nas conversas.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fm_seller.db import Database
from fm_seller.events import normalize as n
from fm_seller.recovery.engine import Opportunity, open_opportunity

log = logging.getLogger("fm_seller.recovery")

# Conversa parada há mais que isso já não é "esfriou", é outra história.
MAX_COLD_AGE_DAYS = 3
# Depois de uma tentativa por conversa, o mesmo contato descansa antes de uma nova.
COOLDOWN_DAYS = 3

# A última mensagem foi nossa e saiu de verdade. Se o vendedor IA passou a conversa para uma pessoa,
# quem deve resposta é a loja, não o cliente: só conta se a última fala foi da pessoa.
_COLD_SQL = """
SELECT c.id AS conv_id, c.tenant_id, c.last_inbound_at, ct.name, ct.phone
FROM conversations c
JOIN tenant_settings s ON s.tenant_id = c.tenant_id AND s.cold_enabled AND s.recovery_enabled
     AND s.consent_declared_at IS NOT NULL
JOIN tenants t ON t.id = c.tenant_id AND t.status = 'active'
JOIN contacts ct ON ct.id = c.contact_id AND ct.phone IS NOT NULL
JOIN LATERAL (
    SELECT m.author, m.status, m.direction, m.created_at FROM messages m
    WHERE m.conversation_id = c.id ORDER BY m.created_at DESC LIMIT 1
) lm ON true
WHERE c.status IN ('bot', 'human') AND c.last_inbound_at IS NOT NULL
  AND lm.direction = 'out' AND lm.status IN ('sent', 'delivered', 'read')
  AND ((c.status = 'bot' AND lm.author = 'bot') OR lm.author = 'human')
  AND lm.created_at <= %(now)s - make_interval(hours => s.cold_after_hours)
  AND lm.created_at >= %(now)s - make_interval(days => %(max_age)s)
  AND NOT EXISTS (
      SELECT 1 FROM recovery_cases rc WHERE rc.tenant_id = c.tenant_id
      AND rc.contact_id = c.contact_id
      AND (rc.status = 'open' OR (rc.source = 'conversa'
           AND rc.opened_at >= %(now)s - make_interval(days => %(cool)s))))
  AND NOT EXISTS (SELECT 1 FROM suppressions sp WHERE sp.tenant_id = c.tenant_id
                  AND sp.identity = ct.phone)
  AND NOT EXISTS (SELECT 1 FROM recovery_sequences rs WHERE rs.tenant_id = c.tenant_id
                  AND rs.trigger_kind = 'conversation_cold' AND NOT rs.enabled)
ORDER BY lm.created_at
LIMIT %(limit)s
"""


def detect_cold_conversations(
    db: Database, *, now: datetime | None = None, limit: int = 200
) -> int:
    """Abre oportunidades para as conversas que esfriaram. Devolve quantas abriu."""
    now = now or datetime.now(UTC)
    with db.tx(system=True) as conn:
        rows = conn.execute(
            _COLD_SQL,
            {"now": now, "max_age": MAX_COLD_AGE_DAYS, "cool": COOLDOWN_DAYS, "limit": limit},
        ).fetchall()
    opened = 0
    for row in rows:
        stamp = int(row["last_inbound_at"].timestamp())
        opp = Opportunity(
            kind=n.CONVERSATION_COLD,
            source="conversa",
            external_ref=f"conv:{row['conv_id']}:{stamp}",
            name=row["name"],
            phone=row["phone"],
        )
        try:
            with db.tx(tenant_id=row["tenant_id"]) as conn:
                case_id, _ = open_opportunity(conn, row["tenant_id"], opp, now=now)
        except Exception:  # defesa: uma conversa com problema não derruba as demais
            log.exception("erro ao abrir oportunidade de conversa", extra={"ctx": {}})
            continue
        opened += 1 if case_id else 0
    return opened
