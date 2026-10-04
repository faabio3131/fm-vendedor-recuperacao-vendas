"""Privacidade (LGPD) na prática: exportar e apagar os dados de um contato, retenção, registro de
consentimento e exclusão da conta no fim do contrato.

Regras que valem aqui:
- nada que identifique a pessoa vai para log, auditoria ou mensagem de erro: a auditoria guarda o id
  interno do contato e contagens, nunca telefone, e-mail, nome ou texto de conversa;
- o pedido de "não contatar" **sobrevive** ao apagamento. Fica só o identificador (telefone ou ID de
  canal), sem nome, sem mensagens, sem ligação com o contato. Não foi possível guardá-lo de forma
  não reversível sem arriscar esquecer o bloqueio quando a chave de cifragem girar (decisão aberta
  para o Diretor e o advogado, ver docs/PENDENCIAS_EXTERNAS.md);
- eventos brutos de checkout (cifrados) não são achados por contato: saem pela retenção.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fm_seller.db import Conn, Database
from fm_seller.errors import bad_request, forbidden
from fm_seller.events.normalize import normalize_phone_br
from fm_seller.provisioning import lifecycle
from fm_seller.services import EDIT_ROLES, Principal, audit

log = logging.getLogger("fm_seller.privacy")

DEFAULT_RETENTION_DAYS = 365  # PROVISÓRIO: decisão do Diretor
MIN_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 3650
PURGE_BATCH = 2000
_CHANNEL_ID = re.compile(r"^(messenger|instagram):([A-Za-z0-9_.-]{1,100})$")


def _guard(p: Principal) -> None:
    if p.role not in EDIT_ROLES:
        raise forbidden("Só dono e administrador podem usar a área de privacidade.")


def record_consent(
    conn: Conn,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    action: str,
    origin: str,
    count: int = 1,
) -> None:
    conn.execute(
        "INSERT INTO consent_events (tenant_id, user_id, action, origin, count) "
        "VALUES (%s, %s, %s, %s, %s)",
        (tenant_id, user_id, action, origin, max(1, count)),
    )


# ---------------------------------------------------------------- contato do titular


def find_contact(conn: Conn, identifier: str) -> uuid.UUID | None:
    """Acha o contato do cliente por telefone, e-mail ou ID de canal (`messenger:123`)."""
    raw = identifier.strip()
    if not raw or len(raw) > 200:
        raise bad_request("invalid_identifier", "Informe um telefone, e-mail ou ID de canal.")
    channel = _CHANNEL_ID.match(raw)
    if channel:
        row = conn.execute(
            "SELECT contact_id AS id FROM contact_channels WHERE channel = %s AND external_id = %s",
            (channel.group(1), channel.group(2)),
        ).fetchone()
    elif "@" in raw:
        row = conn.execute(
            "SELECT id FROM contacts WHERE lower(email) = %s", (raw.lower(),)
        ).fetchone()
    else:
        phone = normalize_phone_br(raw)
        if phone is None:
            raise bad_request("invalid_identifier", "Telefone, e-mail ou ID de canal inválido.")
        row = conn.execute("SELECT id FROM contacts WHERE phone = %s", (phone,)).fetchone()
    return None if row is None else uuid.UUID(str(row["id"]))


def _blocked(
    conn: Conn, contact: dict[str, Any], channels: list[dict[str, Any]]
) -> dict[str, Any] | None:
    identities = [i for i in (contact["phone"], (contact["email"] or "").lower() or None) if i]
    identities += [f"{c['channel']}:{c['external_id']}" for c in channels]
    if not identities:
        return None
    row = conn.execute(
        "SELECT reason, created_at FROM suppressions WHERE identity = ANY(%s) LIMIT 1",
        (identities,),
    ).fetchone()
    return (
        None if row is None else {"reason": row["reason"], "since": row["created_at"].isoformat()}
    )


def export_contact(conn: Conn, p: Principal, identifier: str) -> dict[str, Any] | None:
    """Tudo o que o sistema guarda do contato, para o titular. None se não houver."""
    _guard(p)
    contact_id = find_contact(conn, identifier)
    if contact_id is None:
        return None
    contact = conn.execute(
        "SELECT id, name, phone, email, created_at FROM contacts WHERE id = %s", (contact_id,)
    ).fetchone()
    assert contact is not None
    channels = conn.execute(
        "SELECT channel, external_id FROM contact_channels WHERE contact_id = %s", (contact_id,)
    ).fetchall()
    conversations = []
    for cv in conn.execute(
        "SELECT id, channel, status, handoff_reason, created_at, last_message_at "
        "FROM conversations WHERE contact_id = %s ORDER BY created_at",
        (contact_id,),
    ).fetchall():
        messages = conn.execute(
            "SELECT direction, author, body, status, created_at FROM messages "
            "WHERE conversation_id = %s ORDER BY created_at",
            (cv["id"],),
        ).fetchall()
        conversations.append(
            {
                "channel": cv["channel"],
                "status": cv["status"],
                "motivo_da_transferencia": cv["handoff_reason"],
                "criada_em": cv["created_at"].isoformat(),
                "mensagens": [
                    {
                        "sentido": "recebida" if m["direction"] == "in" else "enviada",
                        "autor": m["author"],
                        "texto": m["body"],
                        "estado": m["status"],
                        "em": m["created_at"].isoformat(),
                    }
                    for m in messages
                ],
            }
        )
    cases = []
    for c in conn.execute(
        "SELECT id, trigger_kind, product_name, amount_cents, status, closed_reason, source, note, "
        "opened_at, closed_at FROM recovery_cases WHERE contact_id = %s ORDER BY opened_at",
        (contact_id,),
    ).fetchall():
        steps = conn.execute(
            "SELECT step_no, template_key, status, sent_at FROM recovery_steps "
            "WHERE case_id = %s ORDER BY step_no",
            (c["id"],),
        ).fetchall()
        cases.append(
            {
                "tipo": c["trigger_kind"],
                "produto": c["product_name"],
                "valor_em_centavos": c["amount_cents"],
                "situacao": c["status"],
                "motivo_do_encerramento": c["closed_reason"],
                "origem": c["source"],
                "observacao": c["note"],
                "aberto_em": c["opened_at"].isoformat(),
                "encerrado_em": c["closed_at"].isoformat() if c["closed_at"] else None,
                "mensagens_de_recuperacao": [
                    {
                        "passo": s["step_no"],
                        "modelo": s["template_key"],
                        "situacao": s["status"],
                        "enviada_em": s["sent_at"].isoformat() if s["sent_at"] else None,
                    }
                    for s in steps
                ],
            }
        )
    audit(
        conn,
        tenant_id=p.tenant_id,
        actor=p.user_id,
        action="privacy.contact_exported",
        target=str(contact_id),
        detail={"conversas": len(conversations), "casos": len(cases)},
    )
    return {
        "gerado_em": datetime.now(UTC).isoformat(),
        "contato": {
            "nome": contact["name"],
            "telefone": contact["phone"],
            "email": contact["email"],
            "criado_em": contact["created_at"].isoformat(),
            "canais": [f"{c['channel']}:{c['external_id']}" for c in channels],
        },
        "nao_contatar": _blocked(conn, contact, channels),
        "conversas": conversations,
        "recuperacao": cases,
    }


def erase_contact(
    conn: Conn, p: Principal, identifier: str, *, also_block: bool = False
) -> dict[str, Any]:
    """Apaga o contato e tudo o que depende dele. Repetir o pedido não dá erro (`found: false`).

    O bloqueio de contato que já existia fica. `also_block` acrescenta um, para quem pediu para
    ser esquecido e também não ser mais procurado (guarda só o identificador)."""
    _guard(p)
    contact_id = find_contact(conn, identifier)
    if contact_id is None:
        return {"found": False}
    contact = conn.execute(
        "SELECT phone, email FROM contacts WHERE id = %s", (contact_id,)
    ).fetchone()
    assert contact is not None
    channels = conn.execute(
        "SELECT channel, external_id FROM contact_channels WHERE contact_id = %s", (contact_id,)
    ).fetchall()
    blocked_before = _blocked(conn, contact, channels) is not None
    if also_block and not blocked_before:
        identity = contact["phone"] or (
            f"{channels[0]['channel']}:{channels[0]['external_id']}"
            if channels
            else (contact["email"] or "").lower()
        )
        conn.execute(
            "INSERT INTO suppressions (tenant_id, identity, reason) VALUES (%s, %s, 'manual') "
            "ON CONFLICT (tenant_id, identity) DO NOTHING",
            (p.tenant_id, identity),
        )
    counts: dict[str, int] = {}
    msgs = conn.execute(
        "SELECT count(*) AS c FROM messages WHERE conversation_id IN "
        "(SELECT id FROM conversations WHERE contact_id = %s)",
        (contact_id,),
    ).fetchone()
    assert msgs is not None
    counts["mensagens"] = int(msgs["c"])
    counts["conversas"] = conn.execute(
        "DELETE FROM conversations WHERE contact_id = %s", (contact_id,)
    ).rowcount
    counts["casos"] = conn.execute(
        "DELETE FROM recovery_cases WHERE contact_id = %s", (contact_id,)
    ).rowcount
    conn.execute("DELETE FROM contacts WHERE id = %s", (contact_id,))
    audit(
        conn,
        tenant_id=p.tenant_id,
        actor=p.user_id,
        action="privacy.contact_erased",
        target=str(contact_id),
        detail={**counts, "bloqueio_mantido": blocked_before or also_block},
    )
    return {"found": True, "erased": counts, "block_kept": blocked_before or also_block}


# ---------------------------------------------------------------- retenção


def get_retention(conn: Conn) -> int:
    row = conn.execute("SELECT retention_days FROM tenant_settings").fetchone()
    return int(row["retention_days"]) if row else DEFAULT_RETENTION_DAYS


def set_retention(conn: Conn, p: Principal, days: int) -> int:
    _guard(p)
    if not MIN_RETENTION_DAYS <= days <= MAX_RETENTION_DAYS:
        raise bad_request(
            "invalid_retention",
            f"A retenção deve ficar entre {MIN_RETENTION_DAYS} e {MAX_RETENTION_DAYS} dias.",
        )
    conn.execute(
        "INSERT INTO tenant_settings (tenant_id, retention_days) VALUES (%s, %s) "
        "ON CONFLICT (tenant_id) DO UPDATE SET retention_days = EXCLUDED.retention_days, "
        "updated_at = now()",
        (p.tenant_id, days),
    )
    audit(
        conn,
        tenant_id=p.tenant_id,
        actor=p.user_id,
        action="privacy.retention",
        detail={"retention_days": days},
    )
    return days


# Um comando por tabela, com o prazo de cada cliente (ou o padrão se ainda não tem ajustes).
_PURGE_SQL = {
    "conversas": (
        "DELETE FROM conversations c WHERE c.id IN (SELECT x.id FROM conversations x "
        "WHERE x.last_message_at < %(now)s - coalesce((SELECT make_interval(days => "
        "s.retention_days) FROM tenant_settings s WHERE s.tenant_id = x.tenant_id), "
        "make_interval(days => %(d)s)) LIMIT %(n)s) RETURNING c.tenant_id"
    ),
    "casos": (
        "DELETE FROM recovery_cases c WHERE c.id IN (SELECT x.id FROM recovery_cases x "
        "WHERE x.status <> 'open' AND x.opened_at < %(now)s - coalesce((SELECT "
        "make_interval(days => s.retention_days) FROM tenant_settings s "
        "WHERE s.tenant_id = x.tenant_id), make_interval(days => %(d)s)) LIMIT %(n)s) "
        "RETURNING c.tenant_id"
    ),
    "eventos": (
        "DELETE FROM webhook_events w WHERE w.id IN (SELECT x.id FROM webhook_events x "
        "WHERE x.received_at < %(now)s - coalesce((SELECT make_interval(days => "
        "s.retention_days) FROM tenant_settings s WHERE s.tenant_id = x.tenant_id), "
        "make_interval(days => %(d)s)) LIMIT %(n)s) RETURNING w.tenant_id"
    ),
    "contatos": (
        "DELETE FROM contacts ct WHERE ct.id IN (SELECT x.id FROM contacts x "
        "WHERE x.created_at < %(now)s - coalesce((SELECT make_interval(days => "
        "s.retention_days) FROM tenant_settings s WHERE s.tenant_id = x.tenant_id), "
        "make_interval(days => %(d)s)) "
        "AND NOT EXISTS (SELECT 1 FROM conversations v WHERE v.contact_id = x.id) "
        "AND NOT EXISTS (SELECT 1 FROM recovery_cases r WHERE r.contact_id = x.id) "
        "LIMIT %(n)s) RETURNING ct.tenant_id"
    ),
}


def purge_retention(db: Database, *, now: datetime | None = None) -> dict[str, int]:
    """Apaga o que passou do prazo de retenção de cada cliente (rotina do worker, em lotes).

    Conversas (com as mensagens), casos já encerrados, eventos brutos recebidos e contatos que
    ficaram sem nada. Caso em andamento nunca é apagado."""
    params = {"now": now or datetime.now(UTC), "d": DEFAULT_RETENTION_DAYS, "n": PURGE_BATCH}
    totals = {kind: 0 for kind in _PURGE_SQL}
    per_tenant: dict[uuid.UUID, dict[str, int]] = {}
    for kind, sql in _PURGE_SQL.items():
        with db.tx(system=True) as conn:
            rows = conn.execute(sql, params).fetchall()
        for r in rows:
            totals[kind] += 1
            bucket = per_tenant.setdefault(r["tenant_id"], {})
            bucket[kind] = bucket.get(kind, 0) + 1
    for tenant_id, counts in per_tenant.items():
        with db.tx(tenant_id=tenant_id, system=True) as conn:
            audit(
                conn,
                tenant_id=tenant_id,
                actor=None,
                action="privacy.retention_purge",
                detail=counts,
            )
    if any(totals.values()):
        log.info("retenção aplicada", extra={"ctx": totals})
    return totals


# ---------------------------------------------------------------- exclusão da conta


def deletion_status(conn: Conn, grace_days: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT deletion_requested_at, deletion_due_at FROM tenant_settings"
    ).fetchone()
    requested = row["deletion_requested_at"] if row else None
    due = row["deletion_due_at"] if row else None
    return {
        "requested_at": requested.isoformat() if requested else None,
        "due_at": due.isoformat() if due else None,
        "grace_days": grace_days,
    }


def request_deletion(db: Database, p: Principal, grace_days: int) -> dict[str, Any]:
    """Pede a exclusão da conta: o serviço é pausado e, passada a carência, tudo é apagado."""
    if p.role != "owner":
        raise forbidden("Só o dono da conta pode pedir a exclusão.")
    now = datetime.now(UTC)
    with db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
        current = conn.execute("SELECT deletion_requested_at FROM tenant_settings").fetchone()
        if current and current["deletion_requested_at"] is not None:
            return deletion_status(conn, grace_days)  # repetir o pedido não muda o prazo
        plan = conn.execute(
            "SELECT status FROM tenant_plans WHERE tenant_id = %s", (p.tenant_id,)
        ).fetchone()
        conn.execute(
            "INSERT INTO tenant_settings (tenant_id, deletion_requested_at, deletion_due_at, "
            "deletion_requested_by, deletion_prev_plan_status) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (tenant_id) DO UPDATE SET "
            "deletion_requested_at = EXCLUDED.deletion_requested_at, "
            "deletion_due_at = EXCLUDED.deletion_due_at, "
            "deletion_requested_by = EXCLUDED.deletion_requested_by, "
            "deletion_prev_plan_status = EXCLUDED.deletion_prev_plan_status, updated_at = now()",
            (
                p.tenant_id,
                now,
                now + timedelta(days=grace_days),
                p.user_id,
                plan["status"] if plan else None,
            ),
        )
        audit(
            conn,
            tenant_id=p.tenant_id,
            actor=p.user_id,
            action="privacy.account_deletion_requested",
            detail={"grace_days": grace_days},
        )
        status = deletion_status(conn, grace_days)
    lifecycle.set_plan_status(db, p.tenant_id, lifecycle.SUSPENDED, actor=p.user_id)
    return status


def cancel_deletion(db: Database, p: Principal, grace_days: int) -> dict[str, Any]:
    if p.role != "owner":
        raise forbidden("Só o dono da conta pode cancelar a exclusão.")
    with db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
        row = conn.execute(
            "SELECT deletion_requested_at, deletion_prev_plan_status FROM tenant_settings"
        ).fetchone()
        if not row or row["deletion_requested_at"] is None:
            return deletion_status(conn, grace_days)
        previous = row["deletion_prev_plan_status"]
        conn.execute(
            "UPDATE tenant_settings SET deletion_requested_at = NULL, deletion_due_at = NULL, "
            "deletion_requested_by = NULL, deletion_prev_plan_status = NULL, updated_at = now()"
        )
        audit(
            conn,
            tenant_id=p.tenant_id,
            actor=p.user_id,
            action="privacy.account_deletion_canceled",
        )
        status = deletion_status(conn, grace_days)
    if previous in ("active", "past_due"):
        lifecycle.set_plan_status(db, p.tenant_id, lifecycle.ACTIVE, actor=p.user_id)
    return status


def purge_due_tenants(db: Database, *, now: datetime | None = None) -> int:
    """Apaga de vez os clientes cuja carência acabou. Devolve quantos foram apagados."""
    stamp = now or datetime.now(UTC)
    with db.tx(system=True) as conn:
        due = conn.execute(
            "SELECT tenant_id, deletion_requested_at FROM tenant_settings "
            "WHERE deletion_due_at IS NOT NULL AND deletion_due_at <= %s",
            (stamp,),
        ).fetchall()
    done = 0
    for row in due:
        tenant_id = row["tenant_id"]
        try:
            with db.tx(system=True) as conn:
                conn.execute("SELECT set_config('app.purge_tenant', %s, true)", (str(tenant_id),))
                locked = conn.execute(
                    "SELECT 1 FROM tenant_settings WHERE tenant_id = %s "
                    "AND deletion_due_at IS NOT NULL AND deletion_due_at <= %s "
                    "FOR UPDATE SKIP LOCKED",
                    (tenant_id, stamp),
                ).fetchone()
                if locked is None:
                    continue
                orphans = [
                    r["user_id"]
                    for r in conn.execute(
                        "SELECT m.user_id FROM memberships m WHERE m.tenant_id = %s AND NOT EXISTS "
                        "(SELECT 1 FROM memberships o WHERE o.user_id = m.user_id "
                        "AND o.tenant_id <> m.tenant_id)",
                        (tenant_id,),
                    ).fetchall()
                ]
                conn.execute("DELETE FROM platform_events WHERE tenant_id = %s", (tenant_id,))
                conn.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
                removed = 0
                if orphans:
                    removed = conn.execute(
                        "DELETE FROM users WHERE id = ANY(%s)", (orphans,)
                    ).rowcount
                conn.execute(
                    "INSERT INTO deletion_log (tenant_id, requested_at, users_removed) "
                    "VALUES (%s, %s, %s)",
                    (tenant_id, row["deletion_requested_at"], removed),
                )
            done += 1
        except Exception:
            log.exception("erro ao excluir cliente", extra={"ctx": {"tenant": str(tenant_id)}})
    return done


# ---------------------------------------------------------------- visão geral


def overview(conn: Conn, p: Principal, grace_days: int) -> dict[str, Any]:
    _guard(p)
    consents = conn.execute(
        "SELECT action, origin, count, created_at, user_id FROM consent_events "
        "ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    names = {
        r["id"]: r["name"] or r["email"]
        for r in conn.execute(
            "SELECT id, name, email FROM users WHERE id IN (SELECT user_id FROM memberships)"
        ).fetchall()
    }
    return {
        "retention_days": get_retention(conn),
        "retention_default": DEFAULT_RETENTION_DAYS,
        "retention_min": MIN_RETENTION_DAYS,
        "retention_max": MAX_RETENTION_DAYS,
        "deletion": deletion_status(conn, grace_days),
        "consents": [
            {
                "action": c["action"],
                "origin": c["origin"],
                "count": c["count"],
                "at": c["created_at"].isoformat(),
                "by": names.get(c["user_id"]),
            }
            for c in consents
        ],
        "terms": "PENDENTE: texto jurídico de termos de uso e política de privacidade (P8).",
    }


__all__ = [
    "cancel_deletion",
    "erase_contact",
    "export_contact",
    "overview",
    "purge_due_tenants",
    "purge_retention",
    "record_consent",
    "request_deletion",
    "set_retention",
]
