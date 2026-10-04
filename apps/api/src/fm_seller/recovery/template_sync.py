"""Envio de templates para aprovação na Meta e sincronização do status.

Fluxo de envio em 3 passos, para nunca segurar transação aberta durante chamada de rede:
1) transação: reserva o nome na Meta (`chave_vN`) e marca 'submitted';
2) chamada à Meta, fora de transação;
3) transação: grava o id e o status devolvidos (ou o motivo da recusa).
Se o resultado da chamada for incerto, o template fica 'submitted' sem id: a sincronização
procura pelo nome na Meta e acerta o status, e um novo envio usa um nome novo (nunca duplica).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fm_seller.channels.templates_gateway import (
    GatewayRejected,
    GatewayUncertain,
    RemoteTemplate,
    TemplateGateway,
)
from fm_seller.db import Database
from fm_seller.errors import AppError, bad_request, not_found
from fm_seller.recovery.service import KEY_RE, RecoveryService
from fm_seller.recovery.templates import examples, meta_name_for, to_meta
from fm_seller.security.crypto import CryptoError, SecretBox
from fm_seller.services import Principal, audit

log = logging.getLogger("fm_seller.templates")

CATEGORIES = ("MARKETING", "UTILITY")
# Status da Meta → status interno. Status desconhecido não altera nada.
STATUS_MAP = {
    "APPROVED": "approved",
    "PENDING": "submitted",
    "IN_APPEAL": "submitted",
    "REJECTED": "rejected",
    "PAUSED": "paused",
    "DISABLED": "disabled",
    "DELETED": "disabled",
    "LIMIT_EXCEEDED": "disabled",
    "ARCHIVED": "disabled",
}
FAST_SYNC_MINUTES = 5  # enquanto há template aguardando a Meta
SLOW_SYNC_MINUTES = 30  # só para perceber pausa ou desativação


def _config(db: Database, box: SecretBox, tenant_id: uuid.UUID) -> dict[str, str]:
    with db.tx(tenant_id=tenant_id) as conn:
        row = conn.execute(
            "SELECT config_encrypted, status FROM connections "
            "WHERE tenant_id = %s AND provider = 'whatsapp_cloud'",
            (tenant_id,),
        ).fetchone()
    if row is None or row["status"] != "connected":
        raise bad_request(
            "channel_not_connected",
            "Conecte e teste o WhatsApp na central de conexões antes de enviar templates.",
        )
    try:
        data = box.decrypt(
            row["config_encrypted"], tenant_id=str(tenant_id), provider="whatsapp_cloud"
        )
    except CryptoError:
        raise bad_request(
            "credential_unreadable", "Não foi possível ler a credencial do WhatsApp."
        ) from None
    return {k: str(v) for k, v in data.items()}


@dataclass
class SyncResult:
    checked: int = 0
    updated: int = 0
    unknown: int = 0


def sync_tenant(
    db: Database, box: SecretBox, gateway: TemplateGateway, tenant_id: uuid.UUID
) -> SyncResult:
    """Consulta a Meta e atualiza o status dos templates do cliente que já foram enviados."""
    result = SyncResult()
    config = _config(db, box, tenant_id)
    try:
        remote = gateway.list(config)
    except GatewayRejected as exc:
        raise bad_request("meta_rejected", f"A Meta recusou a consulta: {exc}") from None
    except GatewayUncertain:
        raise AppError(
            502, "meta_unreachable", "Não foi possível falar com a Meta agora. Tente de novo."
        ) from None
    by_name: dict[tuple[str, str], RemoteTemplate] = {(t.name, t.language): t for t in remote}
    with db.tx(tenant_id=tenant_id) as conn:
        rows = conn.execute(
            "SELECT key, meta_name, meta_language, meta_status, meta_template_id "
            "FROM message_templates WHERE meta_name IS NOT NULL"
        ).fetchall()
        for row in rows:
            result.checked += 1
            item = by_name.get((row["meta_name"], row["meta_language"]))
            if item is None:
                # Na Meta não existe (ainda): fica como está; só marca a consulta.
                conn.execute(
                    "UPDATE message_templates SET meta_synced_at = now() "
                    "WHERE key = %s AND meta_name = %s",
                    (row["key"], row["meta_name"]),
                )
                continue
            new = STATUS_MAP.get(item.status)
            if new is None:
                result.unknown += 1
                log.warning("status de template desconhecido", extra={"ctx": {"s": item.status}})
                new = row["meta_status"]
            conn.execute(
                "UPDATE message_templates SET meta_status = %s, meta_template_id = %s, "
                "meta_category = COALESCE(%s, meta_category), meta_reason = %s, "
                "meta_synced_at = now(), updated_at = CASE WHEN meta_status <> %s "
                "THEN now() ELSE updated_at END WHERE key = %s AND meta_name = %s",
                (
                    new,
                    item.id or row["meta_template_id"],
                    item.category,
                    item.reason if new in ("rejected", "paused", "disabled") else None,
                    new,
                    row["key"],
                    row["meta_name"],
                ),
            )
            if new != row["meta_status"]:
                result.updated += 1
    return result


def sync_all(db: Database, box: SecretBox, gateway: TemplateGateway) -> SyncResult:
    """Rotina do worker: consulta cada cliente com template enviado, sem exagerar nas chamadas."""
    total = SyncResult()
    if not gateway.available:
        return total
    with db.tx(system=True) as conn:
        tenants = conn.execute(
            "SELECT tenant_id FROM message_templates WHERE meta_name IS NOT NULL "
            "GROUP BY tenant_id HAVING "
            "(bool_or(meta_status = 'submitted') AND (min(coalesce(meta_synced_at, "
            "'epoch'::timestamptz)) < now() - make_interval(mins => %s))) OR "
            "min(coalesce(meta_synced_at, 'epoch'::timestamptz)) "
            "< now() - make_interval(mins => %s) LIMIT 100",
            (FAST_SYNC_MINUTES, SLOW_SYNC_MINUTES),
        ).fetchall()
    for row in tenants:
        try:
            part = sync_tenant(db, box, gateway, row["tenant_id"])
        except AppError as exc:
            log.warning(
                "sincronização de templates falhou",
                extra={"ctx": {"tenant": str(row["tenant_id"]), "codigo": exc.code}},
            )
            # Evita insistir a cada ciclo: marca a tentativa.
            with db.tx(tenant_id=row["tenant_id"]) as conn:
                conn.execute(
                    "UPDATE message_templates SET meta_synced_at = now() "
                    "WHERE meta_name IS NOT NULL"
                )
            continue
        total.checked += part.checked
        total.updated += part.updated
        total.unknown += part.unknown
    return total


class TemplateSync(RecoveryService):
    def __init__(self, db: Database, box: SecretBox, gateway: TemplateGateway) -> None:
        super().__init__(db)
        self._box = box
        self._gateway = gateway

    def submit(self, p: Principal, key: str, category: str) -> dict[str, Any]:
        self._guard(p, edit=True)
        if not KEY_RE.match(key):
            raise bad_request("invalid_key", "Chave inválida.")
        if category not in CATEGORIES:
            raise bad_request("invalid_category", "A categoria deve ser MARKETING ou UTILITY.")
        if not self._gateway.available:
            raise bad_request(
                "meta_disabled", "O envio de templates para a Meta ainda não está habilitado."
            )
        config = _config(self._db, self._box, p.tenant_id)
        with self._tx(p) as conn:
            row = conn.execute(
                "SELECT body, meta_status, meta_template_id, meta_version, meta_language "
                "FROM message_templates WHERE key = %s FOR UPDATE",
                (key,),
            ).fetchone()
            if row is None:
                raise not_found("Salve o texto do template antes de enviar para aprovação.")
            waiting = row["meta_status"] == "submitted" and row["meta_template_id"] is None
            if row["meta_status"] not in ("draft", "rejected") and not waiting:
                raise AppError(
                    409,
                    "already_submitted",
                    "Este template já foi enviado. Edite o texto para enviar uma nova versão.",
                )
            text, names = to_meta(row["body"])
            version = row["meta_version"] + 1
            name = meta_name_for(key, version)
            language = row["meta_language"]
            conn.execute(
                "UPDATE message_templates SET meta_version = %s, meta_name = %s, "
                "meta_category = %s, meta_status = 'submitted', meta_template_id = NULL, "
                "meta_reason = NULL, updated_at = now() WHERE key = %s",
                (version, name, category, key),
            )
        note: str | None = None
        remote: RemoteTemplate | None = None
        rejected: str | None = None
        try:
            remote = self._gateway.create(
                config,
                name=name,
                language=language,
                category=category,
                text=text,
                examples=examples(names),
            )
        except GatewayRejected as exc:
            rejected = str(exc)[:300]
        except GatewayUncertain:
            note = "pending_confirmation"
        with self._tx(p) as conn:
            if rejected is not None:
                conn.execute(
                    "UPDATE message_templates SET meta_status = 'rejected', meta_reason = %s, "
                    "updated_at = now() WHERE key = %s AND meta_name = %s",
                    (rejected, key, name),
                )
            elif remote is not None:
                new = STATUS_MAP.get(remote.status, "submitted")
                conn.execute(
                    "UPDATE message_templates SET meta_template_id = %s, meta_status = %s, "
                    "meta_category = COALESCE(%s, meta_category), meta_reason = %s, "
                    "meta_synced_at = now(), updated_at = now() WHERE key = %s AND meta_name = %s",
                    (remote.id or None, new, remote.category, remote.reason, key, name),
                )
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="recovery.template_submit",
                target=key,
                detail={"name": name, "category": category, "rejected": rejected is not None},
            )
        out = next(t for t in self.templates(p) if t["key"] == key)
        out["note"] = note
        return out

    def sync(self, p: Principal) -> dict[str, Any]:
        self._guard(p, edit=True)
        if not self._gateway.available:
            raise bad_request(
                "meta_disabled", "A consulta de templates na Meta ainda não está habilitada."
            )
        r = sync_tenant(self._db, self._box, self._gateway, p.tenant_id)
        with self._tx(p) as conn:
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="recovery.template_sync",
                detail={"checked": r.checked, "updated": r.updated},
            )
        return {"checked": r.checked, "updated": r.updated}
