"""Motor de recuperação de vendas.

Fluxo: o evento de checkout abre um caso e agenda passos; o worker envia os passos devidos.

Regras que não se negociam (todas conferidas de novo NA HORA do envio, não só ao agendar):
- no máximo uma vez: o passo vira `sending` antes de falar com o canal; se o resultado for
  incerto (queda no meio do envio) ele vai para `failed`, nunca é reenviado;
- só envia com recuperação ligada e consentimento declarado pelo cliente (LGPD);
- respeita pedido de não contato, janela de silêncio, limite diário e máximo por caso;
- só envia template que o cliente marcou como aprovado na Meta;
- compra aprovada encerra o caso e cancela o que estava agendado.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fm_seller.channels.whatsapp import upsert_conversation
from fm_seller.db import Conn, Database
from fm_seller.events import normalize as n
from fm_seller.events.normalize import CheckoutEvent
from fm_seller.money import format_brl
from fm_seller.provisioning.lifecycle import blocks_service
from fm_seller.recovery.cases import close_case as _close_case
from fm_seller.recovery.defaults import default_steps
from fm_seller.recovery.senders import MessageSender, OutboundMessage, SendError
from fm_seller.recovery.templates import to_meta
from fm_seller.recovery.timing import (
    in_quiet_hours,
    local_day_start,
    next_allowed,
    next_local_day_open,
)
from fm_seller.security.crypto import CryptoError, SecretBox

log = logging.getLogger("fm_seller.recovery")

# Janela em que uma compra depois do fim da sequência ainda conta como recuperada.
ATTRIBUTION_DAYS = 5  # decisão D6 (04/10/2026): dias após o fim da sequência
STUCK_AFTER = timedelta(hours=1)


@dataclass(frozen=True)
class TenantSettings:
    timezone: str = "America/Sao_Paulo"
    quiet_start: int = 21
    quiet_end: int = 8
    daily_cap: int = 1
    max_contacts_per_case: int = 4
    number_daily_limit: int = 200
    recovery_enabled: bool = False
    consent_declared_at: datetime | None = None

    @property
    def may_send(self) -> bool:
        return self.recovery_enabled and self.consent_declared_at is not None


def load_settings(conn: Conn, tenant_id: uuid.UUID) -> TenantSettings:
    row = conn.execute(
        "SELECT timezone, quiet_start, quiet_end, daily_cap, max_contacts_per_case, "
        "number_daily_limit, recovery_enabled, consent_declared_at "
        "FROM tenant_settings WHERE tenant_id = %s",
        (tenant_id,),
    ).fetchone()
    if row is None:
        return TenantSettings()
    return TenantSettings(
        timezone=row["timezone"],
        quiet_start=row["quiet_start"],
        quiet_end=row["quiet_end"],
        daily_cap=row["daily_cap"],
        max_contacts_per_case=row["max_contacts_per_case"],
        number_daily_limit=row["number_daily_limit"],
        recovery_enabled=row["recovery_enabled"],
        consent_declared_at=row["consent_declared_at"],
    )


def _sequence(conn: Conn, tenant_id: uuid.UUID, kind: str) -> list[dict[str, Any]]:
    row = conn.execute(
        "SELECT enabled, steps FROM recovery_sequences WHERE tenant_id = %s AND trigger_kind = %s",
        (tenant_id, kind),
    ).fetchone()
    if row is None:
        return default_steps(kind)
    return list(row["steps"]) if row["enabled"] else []


def _is_suppressed(conn: Conn, tenant_id: uuid.UUID, phone: str | None, email: str | None) -> bool:
    identities = [i for i in (phone, (email or "").lower() or None) if i]
    if not identities:
        return False
    row = conn.execute(
        "SELECT 1 FROM suppressions WHERE tenant_id = %s AND identity = ANY(%s)",
        (tenant_id, identities),
    ).fetchone()
    return row is not None


def _upsert_contact(
    conn: Conn, tenant_id: uuid.UUID, name: str, phone: str | None, email: str | None
) -> uuid.UUID:
    found = conn.execute(
        "SELECT id FROM contacts WHERE tenant_id = %s AND "
        "(phone = %s OR (%s::text IS NOT NULL AND lower(email) = %s))",
        (tenant_id, phone, email, email),
    ).fetchone()
    if found is None:
        conn.execute(
            "INSERT INTO contacts (tenant_id, name, phone, email) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (tenant_id, name[:200], phone, email),
        )
        found = conn.execute(
            "SELECT id FROM contacts WHERE tenant_id = %s AND "
            "(phone = %s OR (%s::text IS NOT NULL AND lower(email) = %s))",
            (tenant_id, phone, email, email),
        ).fetchone()
    assert found is not None
    contact_id: uuid.UUID = found["id"]
    if name:
        conn.execute(
            "UPDATE contacts SET name = %s WHERE id = %s AND name = ''", (name[:200], contact_id)
        )
    return contact_id


@dataclass(frozen=True)
class Opportunity:
    """Uma venda a recuperar, venha de onde vier (checkout, conversa, registro, planilha)."""

    kind: str  # gatilho: define a sequência (ver `normalize.RECOVERY_TRIGGERS`)
    source: str  # checkout | conversa | manual | importacao
    external_ref: str  # identifica a oportunidade; repetida, não abre outro caso
    name: str
    phone: str | None
    email: str | None = None
    product_name: str = ""
    amount_cents: int = 0
    payment_url: str | None = None
    note: str = ""


# Por que uma oportunidade não virou caso (o painel traduz para o lojista).
NOT_OPENED = ("recovery_off", "no_phone", "suppressed", "no_sequence", "duplicate")


def open_opportunity(
    conn: Conn, tenant_id: uuid.UUID, opp: Opportunity, *, now: datetime | None = None
) -> tuple[uuid.UUID | None, str | None]:
    """Abre o caso e agenda os passos. Devolve (id do caso, None) ou (None, motivo)."""
    now = now or datetime.now(UTC)
    settings = load_settings(conn, tenant_id)
    if not settings.may_send:
        return None, "recovery_off"
    if not opp.phone:  # fase 1: o canal é WhatsApp; sem telefone não há como contatar
        return None, "no_phone"
    if _is_suppressed(conn, tenant_id, opp.phone, opp.email):
        return None, "suppressed"
    steps = _sequence(conn, tenant_id, opp.kind)[: settings.max_contacts_per_case]
    if not steps:
        return None, "no_sequence"

    contact_id = _upsert_contact(conn, tenant_id, opp.name, opp.phone, opp.email)
    case = conn.execute(
        "INSERT INTO recovery_cases (tenant_id, contact_id, trigger_kind, external_ref, "
        "product_name, amount_cents, payment_url, opened_at, source, note) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (tenant_id, trigger_kind, external_ref) DO NOTHING RETURNING id",
        (
            tenant_id,
            contact_id,
            opp.kind,
            opp.external_ref,
            opp.product_name[:200],
            opp.amount_cents,
            opp.payment_url,
            now,
            opp.source,
            opp.note[:300],
        ),
    ).fetchone()
    if case is None:  # a mesma oportunidade já existe: evento ou linha repetida
        return None, "duplicate"
    case_id: uuid.UUID = case["id"]

    # Um caso novo do mesmo contato e produto substitui o anterior (ex.: carrinho → PIX gerado).
    older = conn.execute(
        "SELECT id FROM recovery_cases WHERE tenant_id = %s AND contact_id = %s AND id <> %s "
        "AND status = 'open' AND lower(product_name) = lower(%s)",
        (tenant_id, contact_id, case_id, opp.product_name[:200]),
    ).fetchall()
    for row in older:
        _close_case(conn, row["id"], "stopped", "superseded", now)

    for number, step in enumerate(steps, start=1):
        raw = now + timedelta(minutes=int(step["delay_minutes"]))
        when = next_allowed(raw, settings.timezone, settings.quiet_start, settings.quiet_end)
        conn.execute(
            "INSERT INTO recovery_steps (tenant_id, case_id, step_no, template_key, "
            "scheduled_at, first_scheduled_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (tenant_id, case_id, number, step["template_key"], when, raw),
        )
    return case_id, None


def handle_event(
    conn: Conn, tenant_id: uuid.UUID, event: CheckoutEvent, *, now: datetime | None = None
) -> None:
    """Tratador de eventos de checkout (roda dentro da transação do cliente)."""
    now = now or datetime.now(UTC)
    if event.kind == n.PURCHASE_APPROVED:
        _close_on_purchase(conn, tenant_id, event, now)
        return
    if event.kind not in n.CHECKOUT_TRIGGERS:
        return
    open_opportunity(
        conn,
        tenant_id,
        Opportunity(
            kind=event.kind,
            source="checkout",
            external_ref=event.external_ref,
            name=event.name,
            phone=event.phone,
            email=event.email,
            product_name=event.product_name,
            amount_cents=event.amount_cents,
            payment_url=event.payment_url,
        ),
        now=now,
    )


def _close_on_purchase(conn: Conn, tenant_id: uuid.UUID, ev: CheckoutEvent, now: datetime) -> None:
    if not ev.phone and not ev.email:
        return
    rows = conn.execute(
        "SELECT rc.id, rc.product_name FROM recovery_cases rc "
        "JOIN contacts ct ON ct.id = rc.contact_id "
        "WHERE rc.tenant_id = %s AND (ct.phone = %s OR lower(ct.email) = %s) "
        "AND (rc.status = 'open' OR (rc.status = 'exhausted' AND rc.closed_at > %s))",
        (tenant_id, ev.phone, ev.email, now - timedelta(days=ATTRIBUTION_DAYS)),
    ).fetchall()
    for row in rows:
        name, bought = row["product_name"].casefold(), ev.product_name.casefold()
        if name and bought and name != bought:
            continue  # comprou outro produto: este caso continua
        sent = conn.execute(
            "SELECT count(*) AS c FROM recovery_steps WHERE case_id = %s AND status IN "
            "('sent', 'sending')",
            (row["id"],),
        ).fetchone()
        assert sent is not None
        recovered = sent["c"] > 0
        _close_case(
            conn,
            row["id"],
            "recovered" if recovered else "purchased",
            "purchase_approved",
            now,
            ev.amount_cents if recovered else None,
        )


# ---------------------------------------------------------------- worker


@dataclass
class RunStats:
    claimed: int = 0
    sent: int = 0
    skipped: int = 0
    rescheduled: int = 0
    canceled: int = 0
    failed: int = 0
    reaped: int = 0


@dataclass(frozen=True)
class _Step:
    id: uuid.UUID
    tenant_id: uuid.UUID
    case_id: uuid.UUID
    template_key: str


_VAR = re.compile(r"\{(nome|produto|valor|link)\}")


def _values(name: str, product: str, cents: int, link: str | None) -> dict[str, str]:
    return {
        "nome": (name.split()[:1] or ["cliente"])[0].title(),
        "produto": product or "sua compra",
        "valor": format_brl(cents),
        "link": link or "",
    }


def render(body: str, *, name: str, product: str, cents: int, link: str | None) -> str:
    values = _values(name, product, cents, link)
    return _VAR.sub(lambda m: values[m.group(1)], body).strip()


def _set_step(
    conn: Conn, step_id: uuid.UUID, status: str, detail: str | None, at: datetime | None = None
) -> None:
    if status == "scheduled":
        conn.execute(
            "UPDATE recovery_steps SET status = 'scheduled', scheduled_at = %s, detail = %s "
            "WHERE id = %s",
            (at, detail, step_id),
        )
    else:
        conn.execute(
            "UPDATE recovery_steps SET status = %s, detail = %s WHERE id = %s",
            (status, detail, step_id),
        )


def _finish_if_done(conn: Conn, case_id: uuid.UUID, now: datetime) -> None:
    row = conn.execute(
        "SELECT count(*) AS c FROM recovery_steps WHERE case_id = %s "
        "AND status IN ('scheduled', 'sending')",
        (case_id,),
    ).fetchone()
    assert row is not None
    if row["c"] == 0:
        conn.execute(
            "UPDATE recovery_cases SET status = 'exhausted', closed_reason = 'sequence_finished', "
            "closed_at = %s WHERE id = %s AND status = 'open'",
            (now, case_id),
        )


def reap_stuck(db: Database, now: datetime) -> int:
    """Passo preso em `sending` (queda no meio do envio): resultado incerto, nunca reenvia."""
    with db.tx(system=True) as conn:
        cur = conn.execute(
            "UPDATE recovery_steps SET status = 'failed', detail = 'resultado_incerto' "
            "WHERE status = 'sending' AND claimed_at < %s",
            (now - STUCK_AFTER,),
        )
        return cur.rowcount


def _claim(db: Database, now: datetime, limit: int) -> list[_Step]:
    with db.tx(system=True) as conn:
        rows = conn.execute(
            "UPDATE recovery_steps SET status = 'sending', claimed_at = %s WHERE id IN ("
            "SELECT id FROM recovery_steps WHERE status = 'scheduled' AND scheduled_at <= %s "
            "ORDER BY scheduled_at LIMIT %s FOR UPDATE SKIP LOCKED) "
            "RETURNING id, tenant_id, case_id, template_key",
            (now, now, limit),
        ).fetchall()
    return [_Step(r["id"], r["tenant_id"], r["case_id"], r["template_key"]) for r in rows]


def run_due_steps(
    db: Database,
    box: SecretBox,
    sender: MessageSender,
    *,
    now: datetime | None = None,
    limit: int = 50,
) -> RunStats:
    now = now or datetime.now(UTC)
    stats = RunStats()
    if not sender.available:
        log.warning("envio indisponível nesta instalação: nenhum passo foi pego")
        return stats
    stats.reaped = reap_stuck(db, now)
    for step in _claim(db, now, limit):
        stats.claimed += 1
        try:
            outcome = _process(db, box, sender, step, now)
        except Exception:  # defesa: um passo com problema não derruba os demais
            log.exception("erro inesperado no passo", extra={"ctx": {"step_id": str(step.id)}})
            with db.tx(tenant_id=step.tenant_id) as conn:
                _set_step(conn, step.id, "failed", "erro_interno")
            outcome = "failed"
        setattr(stats, outcome, getattr(stats, outcome) + 1)
    return stats


def _process(
    db: Database, box: SecretBox, sender: MessageSender, step: _Step, now: datetime
) -> str:
    """Devolve o nome do contador de RunStats a incrementar."""
    with db.tx(tenant_id=step.tenant_id) as conn:
        verdict = _check(conn, box, step, now)
        if not isinstance(verdict, _Ready):
            outcome, status, detail, at = verdict
            _set_step(conn, step.id, status, detail, at)
            if status != "scheduled":
                _finish_if_done(conn, step.case_id, now)
            return outcome
        message, config = verdict.message, verdict.config

    try:
        provider_id = sender.send(config, message)
    except SendError as exc:
        status, detail, outcome = "failed", str(exc)[:200], "failed"
        provider_id = None
    except Exception as exc:
        # Resultado incerto: pode ter saído. Não reenvia.
        log.exception("envio com resultado incerto")
        status, detail, outcome = "failed", f"resultado_incerto: {type(exc).__name__}", "failed"
        provider_id = None
    else:
        status, detail, outcome = "sent", None, "sent"

    with db.tx(tenant_id=step.tenant_id) as conn:
        conn.execute(
            "UPDATE recovery_steps SET status = %s, detail = %s, provider_message_id = %s, "
            "sent_at = CASE WHEN %s = 'sent' THEN %s ELSE NULL END WHERE id = %s",
            (status, detail, provider_id, status, now, step.id),
        )
        if status == "sent":  # a conversa passa a mostrar o que foi enviado ao cliente
            _, conv_id = upsert_conversation(conn, step.tenant_id, message.to_phone, "")
            conn.execute(
                "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, "
                "status, provider_message_id) VALUES (%s, %s, 'out', 'recovery', %s, 'sent', %s) "
                "ON CONFLICT DO NOTHING",
                (step.tenant_id, conv_id, message.body, provider_id),
            )
            conn.execute(
                "UPDATE conversations SET last_message_at = now() WHERE id = %s", (conv_id,)
            )
        _finish_if_done(conn, step.case_id, now)
    return outcome


_Decision = tuple[str, str, str | None, datetime | None]  # (contador, status, detalhe, quando)


@dataclass(frozen=True)
class _Ready:
    message: OutboundMessage
    config: dict[str, str]


def _check(conn: Conn, box: SecretBox, step: _Step, now: datetime) -> _Decision | _Ready:
    ctx = conn.execute(
        "SELECT rc.status AS case_status, rc.product_name, rc.amount_cents, rc.payment_url, "
        "rc.contact_id, ct.name, ct.phone, ct.email, t.status AS tenant_status, "
        "tp.status AS plan_status "
        "FROM recovery_cases rc JOIN contacts ct ON ct.id = rc.contact_id "
        "JOIN tenants t ON t.id = rc.tenant_id "
        "LEFT JOIN tenant_plans tp ON tp.tenant_id = rc.tenant_id WHERE rc.id = %s",
        (step.case_id,),
    ).fetchone()
    if ctx is None or ctx["case_status"] != "open":
        return ("canceled", "canceled", "caso_encerrado", None)
    if ctx["tenant_status"] != "active":
        return ("skipped", "skipped", "cliente_suspenso", None)
    if blocks_service(ctx["plan_status"]):
        return ("skipped", "skipped", "assinatura_inativa", None)
    settings = load_settings(conn, step.tenant_id)
    if not settings.may_send:
        return ("skipped", "skipped", "recuperacao_desligada", None)
    if _is_suppressed(conn, step.tenant_id, ctx["phone"], ctx["email"]):
        _close_case(conn, step.case_id, "stopped", "opt_out", now)
        return ("skipped", "skipped", "nao_contatar", None)
    if not ctx["phone"]:
        return ("skipped", "skipped", "sem_telefone", None)

    sent = conn.execute(
        "SELECT count(*) AS c FROM recovery_steps WHERE case_id = %s AND status = 'sent'",
        (step.case_id,),
    ).fetchone()
    assert sent is not None
    if sent["c"] >= settings.max_contacts_per_case:
        return ("skipped", "skipped", "maximo_de_contatos", None)

    tz = settings.timezone
    if in_quiet_hours(now, tz, settings.quiet_start, settings.quiet_end):
        at = next_allowed(now, tz, settings.quiet_start, settings.quiet_end)
        return ("rescheduled", "scheduled", "janela_de_silencio", at)
    today = conn.execute(
        "SELECT count(*) AS c FROM recovery_steps s JOIN recovery_cases c ON c.id = s.case_id "
        "WHERE c.tenant_id = %s AND c.contact_id = %s AND s.status = 'sent' AND s.sent_at >= %s",
        (step.tenant_id, ctx["contact_id"], local_day_start(now, tz)),
    ).fetchone()
    assert today is not None
    if today["c"] >= settings.daily_cap:
        at = next_local_day_open(now, tz, settings.quiet_end)
        return ("rescheduled", "scheduled", "limite_diario", at)

    # A Meta limita os contatos novos por número em 24 h: quem já falou hoje não conta de novo.
    reach = conn.execute(
        "SELECT count(DISTINCT c.contact_id) AS n, coalesce(bool_or(c.contact_id = %s), false) "
        "AS mine FROM recovery_steps s JOIN recovery_cases c ON c.id = s.case_id "
        "WHERE c.tenant_id = %s AND s.status = 'sent' AND s.sent_at >= %s",
        (ctx["contact_id"], step.tenant_id, local_day_start(now, tz)),
    ).fetchone()
    assert reach is not None
    if reach["n"] >= settings.number_daily_limit and not reach["mine"]:
        at = next_local_day_open(now, tz, settings.quiet_end)
        return ("rescheduled", "scheduled", "limite_do_numero", at)

    tpl = conn.execute(
        "SELECT body, meta_status, meta_name, meta_language FROM message_templates "
        "WHERE tenant_id = %s AND key = %s",
        (step.tenant_id, step.template_key),
    ).fetchone()
    if tpl is None or tpl["meta_status"] != "approved":
        return ("skipped", "skipped", "template_nao_aprovado", None)
    conn_row = conn.execute(
        "SELECT config_encrypted, status FROM connections "
        "WHERE tenant_id = %s AND provider = 'whatsapp_cloud'",
        (step.tenant_id,),
    ).fetchone()
    if conn_row is None or conn_row["status"] != "connected":
        return ("skipped", "skipped", "canal_nao_conectado", None)
    try:
        config = box.decrypt(
            conn_row["config_encrypted"], tenant_id=str(step.tenant_id), provider="whatsapp_cloud"
        )
    except CryptoError:
        return ("skipped", "skipped", "credencial_ilegivel", None)

    body = render(
        tpl["body"],
        name=ctx["name"],
        product=ctx["product_name"],
        cents=ctx["amount_cents"],
        link=ctx["payment_url"],
    )
    _, names = to_meta(tpl["body"])
    values = _values(ctx["name"], ctx["product_name"], ctx["amount_cents"], ctx["payment_url"])
    message = OutboundMessage(
        step.tenant_id,
        ctx["phone"],
        step.template_key,
        body,
        meta_name=tpl["meta_name"],
        language=tpl["meta_language"],
        params=tuple(values[n] for n in names),
    )
    return _Ready(message, {k: str(v) for k, v in config.items()})
