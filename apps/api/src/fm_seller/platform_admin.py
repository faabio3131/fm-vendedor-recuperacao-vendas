"""Operação da plataforma: lista de clientes, métricas, saúde e ações seguras e auditadas.

É o único conjunto de rotas de pessoa que lê vários clientes de uma vez, por isso é cercado:
- só entra quem está em `platform_admins` (convite criado pela linha de comando, e-mail do Google);
- fica em `/v1/admin`, fora de qualquer rota de cliente, e a pessoa nem precisa ser de um cliente;
- cada consulta devolve só colunas escolhidas aqui: **nunca** texto de conversa, telefone ou
  e-mail de contato, nem credencial. O e-mail do dono do cliente sai mascarado;
- cada ação grava na auditoria do cliente afetado, com quem fez.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fm_seller.db import Conn, Database
from fm_seller.errors import bad_request, forbidden, not_found, unauthorized
from fm_seller.ops import health
from fm_seller.provisioning import lifecycle
from fm_seller.recovery.senders import MessageSender
from fm_seller.recovery.service import mask_email
from fm_seller.services import audit

log = logging.getLogger("fm_seller.platform_admin")
INVITE_DAYS = 30
PAGE_MAX = 200
ACTIONS_STATUS = {"suspend": lifecycle.SUSPENDED, "reactivate": lifecycle.ACTIVE}


@dataclass(frozen=True)
class Admin:
    user_id: uuid.UUID
    email: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def require_admin(db: Database, token: str | None) -> Admin:
    """Sessão válida de quem é administrador da plataforma. 401 sem sessão, 403 se não for."""
    if not token:
        raise unauthorized()
    with db.tx(session_hash=hash_token(token)) as conn:
        session = conn.execute(
            "SELECT user_id FROM sessions WHERE token_hash = %s AND revoked_at IS NULL "
            "AND expires_at > now()",
            (hash_token(token),),
        ).fetchone()
        if session is None:
            raise unauthorized("Sua sessão expirou. Entre novamente.")
        user_id: uuid.UUID = session["user_id"]
        db.set_user(conn, user_id)
        row = conn.execute(
            "SELECT u.email FROM platform_admins a JOIN users u ON u.id = a.user_id "
            "WHERE a.user_id = %s",
            (user_id,),
        ).fetchone()
    if row is None:
        raise forbidden("Esta área é só para a equipe da plataforma.")
    return Admin(user_id, row["email"])


def _clients_sql() -> str:
    # Colunas escolhidas à mão: nada de conversa, contato ou credencial.
    return (
        "SELECT t.id, t.name, t.status, t.created_at, tp.plan_key, tp.status AS plan_status, "
        "tp.past_due_since, ts.deletion_due_at, "
        "(SELECT u.email FROM memberships m JOIN users u ON u.id = m.user_id "
        " WHERE m.tenant_id = t.id AND m.role = 'owner' ORDER BY m.created_at LIMIT 1) AS owner, "
        "(SELECT count(*) FROM pending_invites i WHERE i.tenant_id = t.id "
        " AND i.accepted_at IS NULL AND i.expires_at > now()) AS invites, "
        "(SELECT coalesce(sum(calls), 0) FROM ai_usage a WHERE a.tenant_id = t.id "
        " AND a.day >= date_trunc('month', now())::date) AS ai_calls, "
        "(SELECT coalesce(sum(failures), 0) FROM ai_usage a WHERE a.tenant_id = t.id "
        " AND a.day >= date_trunc('month', now())::date) AS ai_failures, "
        "(SELECT count(*) FROM messages m WHERE m.tenant_id = t.id AND m.direction = 'out' "
        " AND m.status = 'failed' AND m.created_at >= %(since)s) AS sends_failed, "
        "(SELECT count(*) FROM webhook_events w WHERE w.tenant_id = t.id AND w.status = 'failed' "
        " AND w.received_at >= %(since)s) AS events_failed "
        "FROM tenants t LEFT JOIN tenant_plans tp ON tp.tenant_id = t.id "
        "LEFT JOIN tenant_settings ts ON ts.tenant_id = t.id "
        "ORDER BY t.created_at DESC LIMIT %(limit)s OFFSET %(offset)s"
    )


def list_clients(
    conn: Conn, *, limit: int = 50, offset: int = 0, now: datetime | None = None
) -> dict[str, Any]:
    stamp = now or datetime.now(UTC)
    limit = max(1, min(limit, PAGE_MAX))
    rows = conn.execute(
        _clients_sql(),
        {"since": stamp - timedelta(hours=24), "limit": limit, "offset": max(0, offset)},
    ).fetchall()
    total = conn.execute("SELECT count(*) AS c FROM tenants").fetchone()
    assert total is not None
    plans = conn.execute("SELECT key, display_name FROM plans ORDER BY phase").fetchall()
    return {
        "total": int(total["c"]),
        "plans": [{"key": p["key"], "name": p["display_name"]} for p in plans],
        "items": [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "status": r["status"],
                "created_at": r["created_at"].isoformat(),
                "plan": r["plan_key"],
                "plan_status": r["plan_status"],
                "past_due_since": r["past_due_since"].isoformat() if r["past_due_since"] else None,
                "deletion_due_at": r["deletion_due_at"].isoformat()
                if r["deletion_due_at"]
                else None,
                "owner": mask_email(r["owner"]),
                "pending_invites": int(r["invites"]),
                "ai_replies_month": int(r["ai_calls"]),
                "ai_failures_month": int(r["ai_failures"]),
                "sends_failed_24h": int(r["sends_failed"]),
                "events_failed_24h": int(r["events_failed"]),
            }
            for r in rows
        ],
    }


def _count(conn: Conn, sql: str, params: tuple[object, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return int(next(iter(row.values())))


def metrics(conn: Conn, *, now: datetime | None = None) -> dict[str, Any]:
    """Números do sistema inteiro para quem opera. Contagens e idades, nunca conteúdo."""
    stamp = now or datetime.now(UTC)
    day = stamp - timedelta(hours=24)
    hb = conn.execute(
        "SELECT last_cycle_at, cycles, last_error FROM worker_heartbeat WHERE worker = 'recovery'"
    ).fetchone()
    oldest = conn.execute(
        "SELECT min(created_at) AS t FROM messages WHERE direction = 'out' AND status = 'queued'"
    ).fetchone()
    by_plan = conn.execute(
        "SELECT status, count(*) AS c FROM tenant_plans GROUP BY status"
    ).fetchall()
    return {
        "generated_at": stamp.isoformat(),
        "worker": {
            "cycles": int(hb["cycles"]) if hb else 0,
            "last_cycle_age_s": int((stamp - hb["last_cycle_at"]).total_seconds()) if hb else None,
            "last_error": bool(hb and hb["last_error"]),
        },
        "outbox": {
            "queued": _count(
                conn, "SELECT count(*) FROM messages WHERE direction = 'out' AND status = 'queued'"
            ),
            "oldest_queued_age_s": int((stamp - oldest["t"]).total_seconds())
            if oldest and oldest["t"]
            else None,
            "sent_24h": _count(
                conn,
                "SELECT count(*) FROM messages WHERE direction = 'out' "
                "AND status IN ('sent', 'delivered', 'read') AND created_at >= %s",
                (day,),
            ),
            "failed_24h": _count(
                conn,
                "SELECT count(*) FROM messages WHERE direction = 'out' AND status = 'failed' "
                "AND created_at >= %s",
                (day,),
            ),
        },
        "recovery": {
            "steps_overdue": _count(
                conn,
                "SELECT count(*) FROM recovery_steps WHERE status = 'scheduled' "
                "AND scheduled_at < %s",
                (stamp - health.STEP_LATE,),
            ),
            "steps_sent_24h": _count(
                conn,
                "SELECT count(*) FROM recovery_steps WHERE status = 'sent' AND sent_at >= %s",
                (day,),
            ),
            "steps_failed_24h": _count(
                conn,
                "SELECT count(*) FROM recovery_steps WHERE status = 'failed' AND claimed_at >= %s",
                (day,),
            ),
        },
        "events": {
            "received_24h": _count(
                conn, "SELECT count(*) FROM webhook_events WHERE received_at >= %s", (day,)
            ),
            "failed_24h": _count(
                conn,
                "SELECT count(*) FROM webhook_events WHERE status = 'failed' AND received_at >= %s",
                (day,),
            ),
            "platform_failed": _count(
                conn, "SELECT count(*) FROM platform_events WHERE outcome = 'failed'"
            ),
        },
        "ai": {
            "replies_today": _count(
                conn, "SELECT coalesce(sum(calls), 0) FROM ai_usage WHERE day = %s", (stamp.date(),)
            ),
            "failures_today": _count(
                conn,
                "SELECT coalesce(sum(failures), 0) FROM ai_usage WHERE day = %s",
                (stamp.date(),),
            ),
        },
        "clients": {
            "total": _count(conn, "SELECT count(*) FROM tenants"),
            "by_plan_status": {r["status"]: int(r["c"]) for r in by_plan},
            "deletion_pending": _count(
                conn, "SELECT count(*) FROM tenant_settings WHERE deletion_due_at IS NOT NULL"
            ),
        },
    }


def health_findings(db: Database, sender: MessageSender) -> dict[str, Any]:
    """O mesmo `ops-check` da linha de comando, para o painel da plataforma."""
    found = health.check(db, sender)
    return {
        "ok": not found,
        "exit_code": health.exit_code(found),
        "findings": [{"level": f.level, "code": f.code, "message": f.message} for f in found],
    }


# ---------------------------------------------------------------- ações (auditadas)


def _audit(
    conn: Conn, admin: Admin, tenant_id: uuid.UUID, action: str, detail: dict[str, Any]
) -> None:
    audit(
        conn,
        tenant_id=tenant_id,
        actor=admin.user_id,
        action=f"platform.{action}",
        detail=detail,
    )


def _exists(conn: Conn, tenant_id: uuid.UUID) -> None:
    if conn.execute("SELECT 1 FROM tenants WHERE id = %s", (tenant_id,)).fetchone() is None:
        raise not_found("Cliente não encontrado.")


def set_plan_status(
    db: Database, admin: Admin, tenant_id: uuid.UUID, action: str
) -> dict[str, Any]:
    """Suspende ou reativa o serviço do cliente. Não apaga nada e o login dele continua."""
    if action not in ACTIONS_STATUS:
        raise bad_request("invalid_action", "Ação inválida.")
    new = ACTIONS_STATUS[action]
    with db.tx(system=True) as conn:
        _exists(conn, tenant_id)
        row = conn.execute(
            "SELECT status FROM tenant_plans WHERE tenant_id = %s FOR UPDATE", (tenant_id,)
        ).fetchone()
        if row is None:
            raise bad_request("no_plan", "Este cliente não tem plano.")
        if row["status"] != new:
            lifecycle.set_status(conn, tenant_id, new, target="plataforma", actor=admin.user_id)
        _audit(conn, admin, tenant_id, action, {"from": row["status"], "to": new})
    return {"status": new}


def change_plan(db: Database, admin: Admin, tenant_id: uuid.UUID, plan_key: str) -> dict[str, Any]:
    with db.tx(system=True) as conn:
        _exists(conn, tenant_id)
        if conn.execute("SELECT 1 FROM plans WHERE key = %s", (plan_key,)).fetchone() is None:
            raise bad_request("unknown_plan", "Plano inexistente.")
        row = conn.execute(
            "SELECT plan_key FROM tenant_plans WHERE tenant_id = %s FOR UPDATE", (tenant_id,)
        ).fetchone()
        if row is None:
            raise bad_request("no_plan", "Este cliente não tem plano.")
        conn.execute(
            "UPDATE tenant_plans SET plan_key = %s WHERE tenant_id = %s", (plan_key, tenant_id)
        )
        _audit(conn, admin, tenant_id, "change_plan", {"from": row["plan_key"], "to": plan_key})
    return {"plan": plan_key}


def resend_invite(db: Database, admin: Admin, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Renova a validade dos convites ainda não aceitos. Não envia e-mail: quem comprou entra com
    o Google do e-mail da compra e o convite precisa estar vigente."""
    with db.tx(system=True) as conn:
        _exists(conn, tenant_id)
        renewed = conn.execute(
            "UPDATE pending_invites SET expires_at = %s WHERE tenant_id = %s "
            "AND accepted_at IS NULL",
            (datetime.now(UTC) + timedelta(days=INVITE_DAYS), tenant_id),
        ).rowcount
        if not renewed:
            raise bad_request("no_invite", "Este cliente não tem convite pendente para renovar.")
        _audit(conn, admin, tenant_id, "resend_invite", {"renewed": renewed, "days": INVITE_DAYS})
    return {"renewed": renewed, "days": INVITE_DAYS}


# ---------------------------------------------------------------- convite de administrador


def invite_admin(admin_url: str, email: str, days: int = 7) -> str:
    """Linha de comando (papel dono do banco): quem já existe vira administrador na hora; quem
    ainda não entrou recebe um convite que vale no primeiro login com esse e-mail."""
    import psycopg

    address = email.strip().lower()
    if "@" not in address:
        raise ValueError("E-mail inválido.")
    with psycopg.connect(admin_url) as conn:
        conn.execute("SELECT set_config('app.system', 'on', true)")
        user = conn.execute("SELECT id FROM users WHERE lower(email) = %s", (address,)).fetchone()
        if user is not None:
            conn.execute(
                "INSERT INTO platform_admins (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (user[0],),
            )
            conn.commit()
            return "ja_existia"
        conn.execute(
            "INSERT INTO platform_admin_invites (email, expires_at) VALUES (%s, %s)",
            (address, datetime.now(UTC) + timedelta(days=days)),
        )
        conn.commit()
    return "convite"


def revoke_admin(admin_url: str, email: str) -> bool:
    import psycopg

    address = email.strip().lower()
    with psycopg.connect(admin_url) as conn:
        conn.execute("SELECT set_config('app.system', 'on', true)")
        gone = conn.execute(
            "DELETE FROM platform_admins WHERE user_id IN "
            "(SELECT id FROM users WHERE lower(email) = %s)",
            (address,),
        ).rowcount
        conn.execute(
            "UPDATE platform_admin_invites SET accepted_at = now() WHERE lower(email) = %s "
            "AND accepted_at IS NULL",
            (address,),
        )
        conn.commit()
    return bool(gone)
