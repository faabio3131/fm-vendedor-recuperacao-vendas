"""Serviço de recuperação para a interface (resumo, casos, ajustes, sequências, templates)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fm_seller.db import Database
from fm_seller.errors import AppError, bad_request, forbidden, not_found
from fm_seller.events import normalize as n
from fm_seller.recovery.defaults import (
    DEFAULT_TEMPLATES,
    default_steps,
    validate_steps,
)
from fm_seller.recovery.optout import suppress_identity
from fm_seller.services import EDIT_ROLES, Principal, audit, tenant_features

FEATURE = "recovery.sequences"
KEY_RE = re.compile(r"^[a-z0-9_]{1,60}$")
META_MANUAL = ("submitted", "approved", "rejected")
CASE_STATUSES = ("open", "recovered", "purchased", "stopped", "exhausted")


def mask_phone(phone: str | None) -> str:
    return "" if not phone else f"+{phone[:2]} •• •••••-{phone[-4:]}"


def mask_email(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    user, domain = email.split("@", 1)
    return f"{user[:1]}•••@{domain}"


class RecoveryService:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ---- acesso
    def _guard(self, p: Principal, *, edit: bool = False) -> None:
        _, _, features = tenant_features(self._db, p)
        if FEATURE not in features:
            raise AppError(
                403, "plan_required", "Seu plano atual não inclui recuperação de vendas."
            )
        if edit and p.role not in EDIT_ROLES:
            raise forbidden("Só dono e administrador podem alterar isto.")

    def _tx(self, p: Principal) -> Any:
        return self._db.tx(tenant_id=p.tenant_id, user_id=p.user_id)

    # ---- resumo
    def summary(self, p: Principal, days: int = 30) -> dict[str, Any]:
        self._guard(p)
        since = datetime.now(UTC) - timedelta(days=max(1, min(days, 365)))
        with self._tx(p) as conn:
            cases = conn.execute(
                "SELECT status, count(*) AS c, coalesce(sum(recovered_amount_cents), 0) AS cents "
                "FROM recovery_cases WHERE opened_at >= %s GROUP BY status",
                (since,),
            ).fetchall()
            sent = conn.execute(
                "SELECT count(*) AS c FROM recovery_steps WHERE status = 'sent' AND sent_at >= %s",
                (since,),
            ).fetchone()
            skipped = conn.execute(
                "SELECT detail, count(*) AS c FROM recovery_steps WHERE status = 'skipped' "
                "AND scheduled_at >= %s GROUP BY detail ORDER BY c DESC LIMIT 5",
                (since,),
            ).fetchall()
            conns = conn.execute("SELECT provider, status FROM connections").fetchall()
            approved = conn.execute(
                "SELECT count(*) AS c FROM message_templates WHERE meta_status = 'approved'"
            ).fetchone()
        assert sent is not None and approved is not None
        settings = self.get_settings(p, guard=False)
        status = {r["status"]: r["c"] for r in cases}
        connected = {r["provider"] for r in conns if r["status"] == "connected"}
        return {
            "days": days,
            "cases_total": sum(status.values()),
            "cases_by_status": {s: status.get(s, 0) for s in CASE_STATUSES},
            "recovered_cents": sum(int(r["cents"]) for r in cases),
            "messages_sent": sent["c"],
            "skipped_reasons": [{"reason": r["detail"], "count": r["c"]} for r in skipped],
            "readiness": {
                "whatsapp_connected": "whatsapp_cloud" in connected,
                "checkout_connected": bool(connected & {"cakto", "hotmart"}),
                "approved_templates": approved["c"],
                "consent_declared": settings["consent_declared"],
                "recovery_enabled": settings["recovery_enabled"],
            },
        }

    # ---- casos
    def cases(self, p: Principal, status: str | None, limit: int) -> list[dict[str, Any]]:
        self._guard(p)
        if status is not None and status not in CASE_STATUSES:
            raise bad_request("invalid_status", "Status inválido.")
        with self._tx(p) as conn:
            rows = conn.execute(
                "SELECT c.id, c.trigger_kind, c.product_name, c.amount_cents, c.status, "
                "c.closed_reason, c.recovered_amount_cents, c.opened_at, c.source, c.note, "
                "ct.name, ct.phone, "
                "ct.email, (SELECT count(*) FROM recovery_steps s WHERE s.case_id = c.id "
                "AND s.status = 'sent') AS sent_count "
                "FROM recovery_cases c JOIN contacts ct ON ct.id = c.contact_id "
                "WHERE (%s::text IS NULL OR c.status = %s) ORDER BY c.opened_at DESC LIMIT %s",
                (status, status, max(1, min(limit, 100))),
            ).fetchall()
        return [
            {
                "id": str(r["id"]),
                "trigger": r["trigger_kind"],
                "source": r["source"],
                "note": r["note"],
                "product": r["product_name"],
                "amount_cents": r["amount_cents"],
                "status": r["status"],
                "closed_reason": r["closed_reason"],
                "recovered_cents": r["recovered_amount_cents"],
                "opened_at": r["opened_at"],
                "contact": {
                    "name": r["name"],
                    "phone": mask_phone(r["phone"]),
                    "email": mask_email(r["email"]),
                },
                "messages_sent": r["sent_count"],
            }
            for r in rows
        ]

    # ---- ajustes
    def get_settings(self, p: Principal, *, guard: bool = True) -> dict[str, Any]:
        if guard:
            self._guard(p)
        with self._tx(p) as conn:
            row = conn.execute(
                "SELECT timezone, quiet_start, quiet_end, daily_cap, max_contacts_per_case, "
                "number_daily_limit, recovery_enabled, consent_declared_at, cold_enabled, "
                "cold_after_hours "
                "FROM tenant_settings"
            ).fetchone()
        r = row or {}
        return {
            "timezone": r.get("timezone", "America/Sao_Paulo"),
            "quiet_start": r.get("quiet_start", 21),
            "quiet_end": r.get("quiet_end", 8),
            "daily_cap": r.get("daily_cap", 1),
            "max_contacts_per_case": r.get("max_contacts_per_case", 4),
            "number_daily_limit": r.get("number_daily_limit", 200),
            "recovery_enabled": r.get("recovery_enabled", False),
            "consent_declared": r.get("consent_declared_at") is not None,
            "consent_declared_at": r.get("consent_declared_at"),
            "cold_enabled": r.get("cold_enabled", False),
            "cold_after_hours": r.get("cold_after_hours", 3),
            "can_send": bool(r.get("recovery_enabled", False))
            and r.get("consent_declared_at") is not None,
        }

    def put_settings(self, p: Principal, changes: dict[str, Any]) -> dict[str, Any]:
        self._guard(p, edit=True)
        if "timezone" in changes:
            try:
                ZoneInfo(changes["timezone"])
            except (ZoneInfoNotFoundError, ValueError, OSError):
                raise bad_request("invalid_timezone", "Fuso horário inválido.") from None
        current = self.get_settings(p, guard=False)
        merged = {**current, **{k: v for k, v in changes.items() if v is not None}}
        if changes.get("consent_declared") is False:
            merged["recovery_enabled"] = False  # sem consentimento declarado, desliga
        if merged["recovery_enabled"] and not merged["consent_declared"]:
            raise bad_request(
                "consent_required",
                "Para ligar a recuperação, declare que seus contatos autorizaram receber "
                "mensagens.",
            )
        with self._tx(p) as conn:
            conn.execute(
                "INSERT INTO tenant_settings (tenant_id, timezone, quiet_start, quiet_end, "
                "daily_cap, max_contacts_per_case, recovery_enabled, consent_declared_at, "
                "consent_declared_by, cold_enabled, cold_after_hours, number_daily_limit) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, "
                "CASE WHEN %s THEN COALESCE(%s::timestamptz, now()) END, "
                "CASE WHEN %s THEN %s::uuid END, %s, %s, %s) "
                "ON CONFLICT (tenant_id) DO UPDATE SET timezone = EXCLUDED.timezone, "
                "quiet_start = EXCLUDED.quiet_start, quiet_end = EXCLUDED.quiet_end, "
                "daily_cap = EXCLUDED.daily_cap, "
                "max_contacts_per_case = EXCLUDED.max_contacts_per_case, "
                "recovery_enabled = EXCLUDED.recovery_enabled, "
                "consent_declared_at = EXCLUDED.consent_declared_at, "
                "consent_declared_by = CASE WHEN EXCLUDED.consent_declared_at IS NULL THEN NULL "
                "ELSE COALESCE(tenant_settings.consent_declared_by, EXCLUDED.consent_declared_by) "
                "END, cold_enabled = EXCLUDED.cold_enabled, "
                "cold_after_hours = EXCLUDED.cold_after_hours, "
                "number_daily_limit = EXCLUDED.number_daily_limit, updated_at = now()",
                (
                    p.tenant_id,
                    merged["timezone"],
                    merged["quiet_start"],
                    merged["quiet_end"],
                    merged["daily_cap"],
                    merged["max_contacts_per_case"],
                    merged["recovery_enabled"],
                    merged["consent_declared"],
                    current["consent_declared_at"],
                    merged["consent_declared"],
                    p.user_id,
                    merged["cold_enabled"],
                    merged["cold_after_hours"],
                    merged["number_daily_limit"],
                ),
            )
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="recovery.settings",
                detail={
                    k: merged[k]
                    for k in (
                        "recovery_enabled",
                        "consent_declared",
                        "daily_cap",
                        "max_contacts_per_case",
                        "cold_enabled",
                        "cold_after_hours",
                        "number_daily_limit",
                    )
                },
            )
        return self.get_settings(p, guard=False)

    # ---- sequências
    def sequences(self, p: Principal) -> list[dict[str, Any]]:
        self._guard(p)
        with self._tx(p) as conn:
            rows = {
                r["trigger_kind"]: r
                for r in conn.execute(
                    "SELECT trigger_kind, enabled, steps FROM recovery_sequences"
                ).fetchall()
            }
        out = []
        for kind in n.RECOVERY_TRIGGERS:
            row = rows.get(kind)
            out.append(
                {
                    "trigger": kind,
                    "enabled": True if row is None else row["enabled"],
                    "steps": default_steps(kind) if row is None else row["steps"],
                    "is_default": row is None,
                }
            )
        return out

    def put_sequence(
        self, p: Principal, trigger: str, enabled: bool, steps: object
    ) -> dict[str, Any]:
        self._guard(p, edit=True)
        if trigger not in n.RECOVERY_TRIGGERS:
            raise not_found("Gatilho desconhecido.")
        try:
            clean = validate_steps(steps)
        except ValueError as exc:
            raise bad_request("invalid_sequence", str(exc)) from None
        with self._tx(p) as conn:
            conn.execute(
                "INSERT INTO recovery_sequences (tenant_id, trigger_kind, enabled, steps) "
                "VALUES (%s, %s, %s, %s::jsonb) ON CONFLICT (tenant_id, trigger_kind) "
                "DO UPDATE SET enabled = EXCLUDED.enabled, steps = EXCLUDED.steps, "
                "updated_at = now()",
                (p.tenant_id, trigger, enabled, json.dumps(clean)),
            )
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="recovery.sequence",
                target=trigger,
            )
        return {"trigger": trigger, "enabled": enabled, "steps": clean, "is_default": False}

    def reset_sequence(self, p: Principal, trigger: str) -> None:
        self._guard(p, edit=True)
        if trigger not in n.RECOVERY_TRIGGERS:
            raise not_found("Gatilho desconhecido.")
        with self._tx(p) as conn:
            conn.execute("DELETE FROM recovery_sequences WHERE trigger_kind = %s", (trigger,))
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="recovery.sequence_reset",
                target=trigger,
            )

    # ---- templates
    def templates(self, p: Principal) -> list[dict[str, Any]]:
        self._guard(p)
        with self._tx(p) as conn:
            rows = {
                r["key"]: r
                for r in conn.execute(
                    "SELECT key, body, meta_status, meta_name, meta_reason, meta_category, "
                    "meta_synced_at FROM message_templates"
                ).fetchall()
            }
        out: list[dict[str, Any]] = []
        for key in sorted(set(DEFAULT_TEMPLATES) | set(rows)):
            row = rows.get(key)
            out.append(
                {
                    "key": key,
                    "body": row["body"] if row else DEFAULT_TEMPLATES[key],
                    "meta_status": row["meta_status"] if row else "draft",
                    "meta_name": row["meta_name"] if row else None,
                    "meta_reason": row["meta_reason"] if row else None,
                    "meta_category": row["meta_category"] if row else None,
                    "synced_at": row["meta_synced_at"] if row else None,
                    "saved": row is not None,
                }
            )
        return out

    def put_template(self, p: Principal, key: str, body: str) -> dict[str, Any]:
        self._guard(p, edit=True)
        if not KEY_RE.match(key):
            raise bad_request("invalid_key", "Chave inválida (use letras minúsculas, números e _).")
        body = body.strip()
        if not 1 <= len(body) <= 1024:
            raise bad_request("invalid_body", "O texto deve ter de 1 a 1024 caracteres.")
        with self._tx(p) as conn:
            # Texto alterado perde a aprovação e o vínculo com a Meta: precisa de novo envio
            # (o nome da próxima versão é novo; `meta_version` nunca volta).
            conn.execute(
                "INSERT INTO message_templates (tenant_id, key, body) VALUES (%s, %s, %s) "
                "ON CONFLICT (tenant_id, key) DO UPDATE SET body = EXCLUDED.body, "
                "meta_status = CASE WHEN message_templates.body = EXCLUDED.body "
                "THEN message_templates.meta_status ELSE 'draft' END, "
                "meta_name = CASE WHEN message_templates.body = EXCLUDED.body "
                "THEN message_templates.meta_name END, "
                "meta_template_id = CASE WHEN message_templates.body = EXCLUDED.body "
                "THEN message_templates.meta_template_id END, "
                "meta_reason = CASE WHEN message_templates.body = EXCLUDED.body "
                "THEN message_templates.meta_reason END, updated_at = now()",
                (p.tenant_id, key, body),
            )
            row = conn.execute(
                "SELECT meta_status FROM message_templates WHERE key = %s", (key,)
            ).fetchone()
            audit(
                conn, tenant_id=p.tenant_id, actor=p.user_id, action="recovery.template", target=key
            )
        assert row is not None
        return {"key": key, "body": body, "meta_status": row["meta_status"], "saved": True}

    def set_template_status(self, p: Principal, key: str, status: str) -> dict[str, Any]:
        """O cliente informa o resultado da aprovação feita no painel da Meta (ainda sem API)."""
        self._guard(p, edit=True)
        if status not in META_MANUAL:
            raise bad_request("invalid_status", "Status inválido.")
        with self._tx(p) as conn:
            row = conn.execute(
                "UPDATE message_templates SET meta_status = %s, updated_at = now() "
                "WHERE key = %s RETURNING body",
                (status, key),
            ).fetchone()
            if row is None:
                raise not_found("Salve o texto do template antes de informar o status.")
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="recovery.template_status",
                target=key,
                detail={"status": status},
            )
        return {"key": key, "body": row["body"], "meta_status": status, "saved": True}

    # ---- bloqueios (não contatar)
    def suppressions(self, p: Principal) -> list[dict[str, Any]]:
        self._guard(p)
        with self._tx(p) as conn:
            rows = conn.execute(
                "SELECT id, identity, reason, created_at FROM suppressions "
                "ORDER BY created_at DESC LIMIT 500"
            ).fetchall()
        return [
            {
                "id": str(r["id"]),
                "identity": mask_email(r["identity"])
                if "@" in r["identity"]
                else mask_phone(r["identity"]),
                "reason": r["reason"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def add_suppression(self, p: Principal, raw: str) -> dict[str, str]:
        self._guard(p, edit=True)
        raw = raw.strip()
        identity = raw.lower() if "@" in raw else n.normalize_phone_br(raw)
        if not identity or ("@" in identity and "." not in identity.split("@")[-1]):
            raise bad_request(
                "invalid_identity", "Informe um telefone com DDD ou um e-mail válido."
            )
        with self._tx(p) as conn:
            suppress_identity(conn, p.tenant_id, identity, "manual")
            audit(conn, tenant_id=p.tenant_id, actor=p.user_id, action="recovery.suppression")
        return {"status": "added"}
