"""Ofertas, ajustes do vendedor IA e caixa de conversas."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from fm_seller import onboarding
from fm_seller.ai import usage
from fm_seller.channels.outbound import enqueue_text
from fm_seller.channels.outbox import WINDOW
from fm_seller.db import Database
from fm_seller.errors import AppError, bad_request, forbidden, not_found
from fm_seller.recovery.service import mask_phone
from fm_seller.services import EDIT_ROLES, Principal, audit, tenant_features

FEATURE = "ai.seller"
STATUSES = ("bot", "human", "closed")


def _valid_url(url: str) -> bool:
    parts = urlparse(url)
    return parts.scheme == "https" and bool(parts.hostname) and len(url) <= 500


def person_label(channel: str, phone: str | None, external_id: str | None) -> str:
    """Como mostrar quem é a pessoa: telefone mascarado ou o canal com o final do ID."""
    if channel == "whatsapp":
        return mask_phone(phone)
    name = "Messenger" if channel == "messenger" else "Instagram"
    return f"{name} ••••{external_id[-4:]}" if external_id else name


class SellerService:
    def __init__(self, db: Database, ai_available: bool) -> None:
        self._db = db
        self._ai_available = ai_available

    def _guard(self, p: Principal, *, edit: bool = False) -> None:
        _, _, features = tenant_features(self._db, p)
        if FEATURE not in features:
            raise AppError(403, "plan_required", "Seu plano atual não inclui o vendedor IA.")
        if edit and p.role not in EDIT_ROLES:
            raise forbidden("Só dono e administrador podem alterar isto.")

    def _tx(self, p: Principal) -> Any:
        return self._db.tx(tenant_id=p.tenant_id, user_id=p.user_id)

    # ---- ofertas
    @staticmethod
    def _offer(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(r["id"]),
            "name": r["name"],
            "description": r["description"],
            "price_cents": r["price_cents"],
            "payment_url": r["payment_url"],
            "active": r["active"],
        }

    def offers(self, p: Principal) -> list[dict[str, Any]]:
        self._guard(p)
        with self._tx(p) as conn:
            rows = conn.execute(
                "SELECT id, name, description, price_cents, payment_url, active FROM offers "
                "ORDER BY created_at"
            ).fetchall()
        return [self._offer(r) for r in rows]

    def _check_offer(self, name: str, description: str, price_cents: int, url: str) -> None:
        if not 1 <= len(name.strip()) <= 120:
            raise bad_request("invalid_name", "O nome deve ter de 1 a 120 caracteres.")
        if len(description) > 1000:
            raise bad_request("invalid_description", "A descrição passa de 1000 caracteres.")
        if price_cents < 0 or price_cents > 10_000_000_00:
            raise bad_request("invalid_price", "Preço inválido.")
        if not _valid_url(url):
            raise bad_request("invalid_url", "O link de pagamento precisa começar com https://.")

    def create_offer(
        self, p: Principal, name: str, description: str, price_cents: int, url: str
    ) -> dict[str, Any]:
        self._guard(p, edit=True)
        self._check_offer(name, description, price_cents, url)
        with self._tx(p) as conn:
            row = conn.execute(
                "INSERT INTO offers (tenant_id, name, description, price_cents, payment_url) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id, name, description, price_cents, "
                "payment_url, active",
                (p.tenant_id, name.strip(), description.strip(), price_cents, url),
            ).fetchone()
            assert row is not None
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="offer.created",
                target=name[:80],
            )
        return self._offer(row)

    def update_offer(
        self,
        p: Principal,
        offer_id: uuid.UUID,
        name: str,
        description: str,
        price_cents: int,
        url: str,
        active: bool,
    ) -> dict[str, Any]:
        self._guard(p, edit=True)
        self._check_offer(name, description, price_cents, url)
        with self._tx(p) as conn:
            row = conn.execute(
                "UPDATE offers SET name = %s, description = %s, price_cents = %s, "
                "payment_url = %s, active = %s, updated_at = now() WHERE id = %s "
                "RETURNING id, name, description, price_cents, payment_url, active",
                (name.strip(), description.strip(), price_cents, url, active, offer_id),
            ).fetchone()
            if row is None:
                raise not_found("Oferta não encontrada.")
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="offer.updated",
                target=str(offer_id),
            )
        return self._offer(row)

    def delete_offer(self, p: Principal, offer_id: uuid.UUID) -> None:
        self._guard(p, edit=True)
        with self._tx(p) as conn:
            gone = conn.execute(
                "DELETE FROM offers WHERE id = %s RETURNING id", (offer_id,)
            ).fetchone()
            if gone is None:
                raise not_found("Oferta não encontrada.")
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="offer.deleted",
                target=str(offer_id),
            )

    # ---- ajustes
    def get_settings(self, p: Principal) -> dict[str, Any]:
        self._guard(p)
        with self._tx(p) as conn:
            row = conn.execute("SELECT ai_enabled, ai_persona FROM tenant_settings").fetchone()
            offers = conn.execute("SELECT count(*) AS c FROM offers WHERE active").fetchone()
        assert offers is not None
        return {
            "ai_enabled": bool(row and row["ai_enabled"]),
            "ai_persona": row["ai_persona"] if row else "",
            "active_offers": offers["c"],
            "ai_available": self._ai_available,
        }

    def usage(self, p: Principal) -> dict[str, Any]:
        self._guard(p)
        with self._tx(p) as conn:
            row = conn.execute("SELECT timezone FROM tenant_settings").fetchone()
            return usage.summary(conn, p.tenant_id, row["timezone"] if row else "America/Sao_Paulo")

    def put_settings(self, p: Principal, ai_enabled: bool, ai_persona: str) -> dict[str, Any]:
        self._guard(p, edit=True)
        if len(ai_persona) > 600:
            raise bad_request("invalid_persona", "O tom de voz passa de 600 caracteres.")
        with self._tx(p) as conn:
            if ai_enabled:
                row = conn.execute("SELECT ai_enabled FROM tenant_settings").fetchone()
                if not (row and row["ai_enabled"]):
                    onboarding.require_for_ai(conn)
            conn.execute(
                "INSERT INTO tenant_settings (tenant_id, ai_enabled, ai_persona) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (tenant_id) DO UPDATE SET ai_enabled = EXCLUDED.ai_enabled, "
                "ai_persona = EXCLUDED.ai_persona, updated_at = now()",
                (p.tenant_id, ai_enabled, ai_persona.strip()),
            )
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="seller.settings",
                detail={"ai_enabled": ai_enabled},
            )
        return self.get_settings(p)

    # ---- caixa de conversas
    def inbox(self, p: Principal, status: str | None, limit: int) -> list[dict[str, Any]]:
        self._guard(p)
        if status is not None and status not in STATUSES:
            raise bad_request("invalid_status", "Status inválido.")
        with self._tx(p) as conn:
            rows = conn.execute(
                "SELECT cv.id, cv.status, cv.handoff_reason, cv.last_inbound_at, "
                "cv.last_message_at, "
                "cv.channel, ct.name, ct.phone, cc.external_id, "
                "(SELECT body FROM messages m WHERE m.conversation_id = cv.id "
                "ORDER BY m.created_at DESC LIMIT 1) AS preview "
                "FROM conversations cv JOIN contacts ct ON ct.id = cv.contact_id "
                "LEFT JOIN contact_channels cc ON cc.contact_id = ct.id "
                "AND cc.channel = cv.channel "
                "WHERE (%s::text IS NULL OR cv.status = %s) "
                "ORDER BY (cv.status = 'human') DESC, cv.last_message_at DESC LIMIT %s",
                (status, status, max(1, min(limit, 100))),
            ).fetchall()
        now = datetime.now(UTC)
        return [
            {
                "id": str(r["id"]),
                "status": r["status"],
                "handoff_reason": r["handoff_reason"],
                "channel": r["channel"],
                "name": r["name"],
                "phone": person_label(r["channel"], r["phone"], r["external_id"]),
                "preview": (r["preview"] or "")[:160],
                "last_message_at": r["last_message_at"],
                "window_open": r["last_inbound_at"] is not None
                and now - r["last_inbound_at"] <= WINDOW,
            }
            for r in rows
        ]

    def messages(self, p: Principal, conv_id: uuid.UUID) -> dict[str, Any]:
        self._guard(p)
        with self._tx(p) as conn:
            conv = conn.execute(
                "SELECT cv.status, cv.channel, cv.last_inbound_at, ct.name, ct.phone, "
                "cc.external_id FROM conversations cv "
                "JOIN contacts ct ON ct.id = cv.contact_id "
                "LEFT JOIN contact_channels cc ON cc.contact_id = ct.id "
                "AND cc.channel = cv.channel WHERE cv.id = %s",
                (conv_id,),
            ).fetchone()
            if conv is None:
                raise not_found("Conversa não encontrada.")
            rows = conn.execute(
                "SELECT id, direction, author, body, status, error, created_at FROM messages "
                "WHERE conversation_id = %s ORDER BY created_at DESC LIMIT 100",
                (conv_id,),
            ).fetchall()
        now = datetime.now(UTC)
        return {
            "status": conv["status"],
            "channel": conv["channel"],
            "name": conv["name"],
            "phone": person_label(conv["channel"], conv["phone"], conv["external_id"]),
            "window_open": conv["last_inbound_at"] is not None
            and now - conv["last_inbound_at"] <= WINDOW,
            "messages": [
                {
                    "id": str(r["id"]),
                    "direction": r["direction"],
                    "author": r["author"],
                    "body": r["body"],
                    "status": r["status"],
                    "error": r["error"],
                    "created_at": r["created_at"],
                }
                for r in reversed(rows)
            ],
        }

    def reply(self, p: Principal, conv_id: uuid.UUID, body: str) -> dict[str, str]:
        self._guard(p)
        body = body.strip()
        if not 1 <= len(body) <= 1000:
            raise bad_request("invalid_body", "A mensagem deve ter de 1 a 1000 caracteres.")
        with self._tx(p) as conn:
            conv = conn.execute(
                "SELECT channel, last_inbound_at FROM conversations WHERE id = %s FOR UPDATE",
                (conv_id,),
            ).fetchone()
            if conv is None:
                raise not_found("Conversa não encontrada.")
            if conv["last_inbound_at"] is None or datetime.now(UTC) - conv[
                "last_inbound_at"
            ] > timedelta(hours=24):
                raise bad_request(
                    "window_closed",
                    "Passaram mais de 24 h desde a última mensagem do cliente: "
                    + (
                        "só template aprovado."
                        if conv["channel"] == "whatsapp"
                        else "a Meta não permite resposta livre fora dessa janela."
                    ),
                )
            enqueue_text(conn, p.tenant_id, conv_id, "human", body)
            conn.execute(
                "UPDATE conversations SET status = 'human', last_message_at = now() WHERE id = %s",
                (conv_id,),
            )
        return {"status": "queued"}

    def set_status(self, p: Principal, conv_id: uuid.UUID, status: str) -> dict[str, str]:
        self._guard(p)
        if status not in STATUSES:
            raise bad_request("invalid_status", "Status inválido.")
        with self._tx(p) as conn:
            row = conn.execute(
                "UPDATE conversations SET status = %s, handoff_reason = CASE WHEN %s = 'bot' "
                "THEN NULL ELSE handoff_reason END WHERE id = %s RETURNING id",
                (status, status, conv_id),
            ).fetchone()
            if row is None:
                raise not_found("Conversa não encontrada.")
        return {"status": status}
