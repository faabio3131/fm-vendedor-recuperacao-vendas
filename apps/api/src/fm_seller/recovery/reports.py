"""Relatório de recuperação: funil e valor recuperado, por período e agrupamento.

Os números vêm do que o sistema registrou (casos, passos enviados, estado de entrega informado pelo
provedor, respostas de quem escreveu). Não são medição de mercado e não projetam resultado. A
atribuição não é reinterpretada aqui: "recuperada" é o estado que o motor já gravou, pela regra D6
(janela de `ATTRIBUTION_DAYS` dias, último toque, mensagem enviada, mesmo produto).

Definições:
- caso: oportunidade aberta no período (`opened_at`), no fuso do cliente;
- com mensagem: caso com ao menos um passo enviado de verdade;
- entregue / lida: estado informado pelo WhatsApp para o passo (nunca a intenção de envio);
- respondeu: o contato escreveu no WhatsApp depois da primeira mensagem enviada e até o fim do caso;
- recuperada: caso `recovered`; "comprou sem mensagem" (`purchased`) não é atribuído ao sistema.
A recuperação por template é só WhatsApp; o bloco `channels` mostra a conversa por canal.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fm_seller.db import Conn
from fm_seller.errors import bad_request

GROUPS = ("none", "day", "product", "sequence", "source")
MAX_DAYS = 366
DEFAULT_DAYS = 30

_KEY = {
    "none": "'total'",
    "day": "c.day::text",
    "product": "coalesce(nullif(c.product_name, ''), '(sem produto)')",
    "sequence": "c.trigger_kind",
    "source": "c.source",
}

_SQL = """
WITH c AS (
  SELECT rc.id, rc.contact_id, rc.status, rc.trigger_kind, rc.source, rc.product_name,
         rc.recovered_amount_cents, rc.closed_at,
         (rc.opened_at AT TIME ZONE %(tz)s)::date AS day
  FROM recovery_cases rc
  WHERE rc.tenant_id = %(tenant)s AND rc.opened_at >= %(start)s AND rc.opened_at < %(end)s
), st AS (
  SELECT s.case_id,
         count(*) FILTER (WHERE s.status = 'sent') AS sent,
         count(*) FILTER (WHERE s.status = 'sent'
                          AND s.delivery_status IN ('delivered', 'read')) AS delivered,
         count(*) FILTER (WHERE s.status = 'sent' AND s.delivery_status = 'read') AS read,
         min(s.sent_at) FILTER (WHERE s.status = 'sent') AS first_sent
  FROM recovery_steps s JOIN c ON c.id = s.case_id GROUP BY s.case_id
), r AS (
  SELECT c.id AS case_id,
         EXISTS (
           SELECT 1 FROM conversations cv JOIN messages m ON m.conversation_id = cv.id
           WHERE cv.tenant_id = %(tenant)s AND cv.contact_id = c.contact_id
             AND cv.channel = 'whatsapp' AND m.direction = 'in' AND m.author = 'customer'
             AND m.created_at > st.first_sent AND m.created_at <= coalesce(c.closed_at, now())
         ) AS replied
  FROM c JOIN st ON st.case_id = c.id WHERE st.first_sent IS NOT NULL
)
SELECT {key} AS grp,
       count(*) AS cases,
       count(*) FILTER (WHERE coalesce(st.sent, 0) > 0) AS with_message,
       coalesce(sum(st.sent), 0) AS messages_sent,
       coalesce(sum(st.delivered), 0) AS delivered,
       coalesce(sum(st.read), 0) AS read,
       count(*) FILTER (WHERE r.replied) AS replied,
       count(*) FILTER (WHERE c.status = 'recovered') AS recovered,
       coalesce(sum(c.recovered_amount_cents) FILTER (WHERE c.status = 'recovered'), 0)
         AS recovered_cents,
       count(*) FILTER (WHERE c.status = 'purchased') AS purchased_without_message,
       count(*) FILTER (WHERE c.status = 'stopped') AS stopped,
       count(*) FILTER (WHERE c.status = 'exhausted') AS exhausted,
       count(*) FILTER (WHERE c.status = 'open') AS open
FROM c LEFT JOIN st ON st.case_id = c.id LEFT JOIN r ON r.case_id = c.id
GROUP BY grp ORDER BY grp
"""

_COUNTS = (
    "cases",
    "with_message",
    "messages_sent",
    "delivered",
    "read",
    "replied",
    "recovered",
    "recovered_cents",
    "purchased_without_message",
    "stopped",
    "exhausted",
    "open",
)


def parse_period(
    tz: str, start: date | None, end: date | None, today: date | None = None
) -> tuple[date, date]:
    """Período em datas do fuso do cliente. Padrão: últimos 30 dias, até hoje."""
    now_day = today or datetime.now(ZoneInfo(tz)).date()
    end = end or now_day
    start = start or end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        raise bad_request("invalid_period", "A data inicial é depois da final.")
    if (end - start).days + 1 > MAX_DAYS:
        raise bad_request("invalid_period", f"O período pode ter no máximo {MAX_DAYS} dias.")
    return start, end


def _rates(row: dict[str, Any]) -> dict[str, Any]:
    sent = row["with_message"]
    row["recovery_rate"] = round(row["recovered"] / sent, 4) if sent else None
    row["reply_rate"] = round(row["replied"] / sent, 4) if sent else None
    return row


def build(
    conn: Conn,
    tenant_id: uuid.UUID,
    tz: str,
    start: date,
    end: date,
    group_by: str = "none",
) -> dict[str, Any]:
    if group_by not in GROUPS:
        raise bad_request("invalid_group", "Agrupamento inválido.")
    zone = ZoneInfo(tz)
    lo = datetime.combine(start, datetime.min.time(), zone)
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), zone)
    params = {"tenant": tenant_id, "tz": tz, "start": lo, "end": hi}
    rows = conn.execute(_SQL.format(key=_KEY[group_by]), params).fetchall()
    groups = [_rates({"group": r["grp"], **{k: int(r[k]) for k in _COUNTS}}) for r in rows]
    total = _rates({"group": "total", **{k: sum(g[k] for g in groups) for k in _COUNTS}})
    channels = conn.execute(
        "SELECT cv.channel, count(DISTINCT cv.id) AS conversations, "
        "count(*) FILTER (WHERE m.direction = 'in') AS inbound, "
        "count(*) FILTER (WHERE m.direction = 'out') AS outbound "
        "FROM conversations cv JOIN messages m ON m.conversation_id = cv.id "
        "WHERE cv.tenant_id = %s AND m.created_at >= %s AND m.created_at < %s "
        "GROUP BY cv.channel ORDER BY cv.channel",
        (tenant_id, lo, hi),
    ).fetchall()
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "timezone": tz,
        "group_by": group_by,
        "total": total,
        "groups": [] if group_by == "none" else groups,
        "channels": [
            {
                "channel": c["channel"],
                "conversations": int(c["conversations"]),
                "inbound": int(c["inbound"]),
                "outbound": int(c["outbound"]),
            }
            for c in channels
        ],
    }


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: Any) -> Any:
    """Texto que o Excel/Planilhas leria como fórmula ganha um apóstrofo na frente."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


_HEADER = (
    "grupo",
    "casos",
    "com_mensagem",
    "mensagens_enviadas",
    "entregues",
    "lidas",
    "responderam",
    "recuperadas",
    "valor_recuperado_centavos",
    "compraram_sem_mensagem",
    "parados",
    "esgotados",
    "abertos",
    "taxa_recuperacao",
    "taxa_resposta",
)


def to_csv(report: dict[str, Any]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(_HEADER)
    rows = report["groups"] or [report["total"]]
    for g in rows:
        writer.writerow(
            [csv_cell(g["group"])]
            + [g[k] for k in _COUNTS]
            + ["" if g[k] is None else g[k] for k in ("recovery_rate", "reply_rate")]
        )
    return out.getvalue()
