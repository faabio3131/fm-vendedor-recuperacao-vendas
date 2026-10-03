"""Vendedor IA: responde quem escreveu, dentro de limites que o modelo não consegue contornar.

Regras:
- preço, nome da oferta e link de pagamento só entram a partir do cadastro (placeholders);
  texto do modelo com URL ou valor em reais é recusado e a conversa vai para uma pessoa;
- pedido explícito de falar com pessoa, conversa sem ofertas ativas, IA desligada/indisponível
  ou excesso de respostas por hora transferem para uma pessoa;
- quem pediu para não ser contatado nunca recebe resposta;
- a resposta é enfileirada na mesma transação que marca a mensagem como tratada: no máximo uma.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fm_seller.ai import usage
from fm_seller.ai.model import AiContext, AiModel, AiReply, OfferView
from fm_seller.channels.whatsapp import enqueue_text
from fm_seller.db import Conn, Database
from fm_seller.money import format_brl
from fm_seller.recovery.optout import normalize

log = logging.getLogger("fm_seller.seller")
HANDOFF_MESSAGE = "Certo! Vou chamar uma pessoa da equipe para continuar com você."
MAX_BOT_REPLIES_PER_HOUR = 10
HISTORY = 10
_HUMAN = ("atendente", "humano", "pessoa", "falar com alguem", "gerente", "responsavel")
_FORBIDDEN = re.compile(r"https?://|www\.|r\$|\breais\b|\bpix\b.*\bchave\b", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\{(oferta|preco|link)\}")


@dataclass
class SellerStats:
    conversations: int = 0
    replied: int = 0
    handoffs: int = 0
    ignored: int = 0


def asks_for_human(text: str) -> bool:
    norm = normalize(text)
    return any(k in norm for k in _HUMAN)


def render_reply(reply: AiReply, offers: dict[str, tuple[str, str, str]]) -> str | None:
    """Preenche os placeholders com dados do cadastro. None = resposta inválida (recusada)."""
    raw = reply.text.strip()
    if not raw or len(raw) > 1000 or _FORBIDDEN.search(raw):
        return None
    wanted = set(_PLACEHOLDER.findall(raw))
    if wanted:
        if reply.offer_id is None or reply.offer_id not in offers:
            return None
        name, price, link = offers[reply.offer_id]
        values = {"oferta": name, "preco": price, "link": link}
        raw = _PLACEHOLDER.sub(lambda m: values[m.group(1)], raw)
    elif "{" in raw or "}" in raw:
        return None
    return raw


def _handoff(
    conn: Conn, tenant_id: uuid.UUID, conv_id: uuid.UUID, reason: str, notify: bool
) -> None:
    conn.execute(
        "UPDATE conversations SET status = 'human', handoff_reason = %s WHERE id = %s",
        (reason, conv_id),
    )
    if notify:
        enqueue_text(conn, tenant_id, conv_id, "bot", HANDOFF_MESSAGE)


def run_ai_replies(
    db: Database, model: AiModel, *, now: datetime | None = None, limit: int = 50
) -> SellerStats:
    now = now or datetime.now(UTC)
    stats = SellerStats()
    with db.tx(system=True) as conn:
        pending = conn.execute(
            "SELECT DISTINCT m.tenant_id, m.conversation_id FROM messages m "
            "JOIN conversations cv ON cv.id = m.conversation_id "
            "WHERE m.direction = 'in' AND NOT m.handled AND cv.status = 'bot' LIMIT %s",
            (limit,),
        ).fetchall()
    for row in pending:
        try:
            with db.tx(tenant_id=row["tenant_id"]) as conn:
                outcome = _handle_conversation(
                    conn, model, row["tenant_id"], row["conversation_id"], now
                )
        except Exception:
            log.exception(
                "erro no vendedor IA", extra={"ctx": {"conv": str(row["conversation_id"])}}
            )
            continue
        stats.conversations += 1
        setattr(stats, outcome, getattr(stats, outcome) + 1)
    return stats


def _handle_conversation(
    conn: Conn, model: AiModel, tenant_id: uuid.UUID, conv_id: uuid.UUID, now: datetime
) -> str:
    locked = conn.execute(
        "SELECT cv.status, cv.contact_id, ct.name, ct.phone FROM conversations cv "
        "JOIN contacts ct ON ct.id = cv.contact_id WHERE cv.id = %s FOR UPDATE OF cv SKIP LOCKED",
        (conv_id,),
    ).fetchone()
    if locked is None or locked["status"] != "bot":
        return "ignored"
    inbound = conn.execute(
        "UPDATE messages SET handled = true WHERE conversation_id = %s AND direction = 'in' "
        "AND NOT handled RETURNING body",
        (conv_id,),
    ).fetchall()
    if not inbound:
        return "ignored"
    if conn.execute(
        "SELECT 1 FROM suppressions WHERE tenant_id = %s AND identity = %s",
        (tenant_id, locked["phone"]),
    ).fetchone():
        return "ignored"

    settings = conn.execute(
        "SELECT ai_enabled, ai_persona, timezone FROM tenant_settings WHERE tenant_id = %s",
        (tenant_id,),
    ).fetchone()
    if settings is None or not settings["ai_enabled"]:
        _handoff(conn, tenant_id, conv_id, "ia_desligada", notify=False)
        return "handoffs"
    if not model.available:
        _handoff(conn, tenant_id, conv_id, "ia_indisponivel", notify=False)
        return "handoffs"
    if any(asks_for_human(r["body"]) for r in inbound):
        _handoff(conn, tenant_id, conv_id, "pediu_atendente", notify=True)
        return "handoffs"
    recent = conn.execute(
        "SELECT count(*) AS c FROM messages WHERE conversation_id = %s AND direction = 'out' "
        "AND author = 'bot' AND created_at > %s",
        (conv_id, now - timedelta(hours=1)),
    ).fetchone()
    assert recent is not None
    if recent["c"] >= MAX_BOT_REPLIES_PER_HOUR:
        _handoff(conn, tenant_id, conv_id, "limite_de_respostas", notify=False)
        return "handoffs"

    tz = settings["timezone"]
    limit = usage.monthly_limit(conn, tenant_id)
    if limit is not None and usage.month_usage(conn, tenant_id, tz)["calls"] >= limit:
        _handoff(conn, tenant_id, conv_id, "limite_do_plano", notify=True)
        return "handoffs"

    offer_rows = conn.execute(
        "SELECT id, name, description, price_cents, payment_url FROM offers "
        "WHERE tenant_id = %s AND active ORDER BY created_at LIMIT 20",
        (tenant_id,),
    ).fetchall()
    if not offer_rows:
        _handoff(conn, tenant_id, conv_id, "sem_ofertas", notify=True)
        return "handoffs"
    registry = {
        str(o["id"]): (o["name"], format_brl(o["price_cents"]), o["payment_url"])
        for o in offer_rows
    }
    history = conn.execute(
        "SELECT direction, body FROM messages WHERE conversation_id = %s "
        "ORDER BY created_at DESC LIMIT %s",
        (conv_id, HISTORY),
    ).fetchall()
    ctx = AiContext(
        persona=settings["ai_persona"],
        customer_name=(locked["name"] or "").split(" ")[0],
        offers=tuple(
            OfferView(str(o["id"]), o["name"], o["description"], format_brl(o["price_cents"]))
            for o in offer_rows
        ),
        history=tuple(
            ("customer" if h["direction"] == "in" else "assistant", h["body"])
            for h in reversed(history)
        ),
    )
    try:
        reply = model.reply(ctx)
    except Exception as exc:
        # Sem isto a transação voltaria e a mesma mensagem seria tentada para sempre, sem resposta.
        log.warning(
            "modelo falhou", extra={"ctx": {"erro": type(exc).__name__, "motivo": str(exc)[:120]}}
        )
        usage.record(conn, tenant_id, tz, ok=False)
        _handoff(conn, tenant_id, conv_id, "erro_do_modelo", notify=True)
        return "handoffs"
    usage.record(
        conn, tenant_id, tz, ok=True, tokens_in=reply.tokens_in, tokens_out=reply.tokens_out
    )
    if reply.handoff:
        _handoff(conn, tenant_id, conv_id, reply.handoff_reason or "modelo_pediu", notify=True)
        return "handoffs"
    text = render_reply(reply, registry)
    if text is None:
        _handoff(conn, tenant_id, conv_id, "resposta_invalida", notify=True)
        return "handoffs"
    enqueue_text(conn, tenant_id, conv_id, "bot", text)
    conn.execute("UPDATE conversations SET last_message_at = now() WHERE id = %s", (conv_id,))
    return "replied"
