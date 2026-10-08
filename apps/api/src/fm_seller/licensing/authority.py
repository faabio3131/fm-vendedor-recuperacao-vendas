"""Troca de autoridade comercial de UMA assinatura: de direta (Cakto/Hotmart) para o Command.

Só com **aprovação humana registrada** (quem aprovou e a evidência), só em
`FM_FMCOMMAND_MODE=enforce` e nunca criando conta, plano nem cobrança: liga o tenant que já existe
à licença do Command, pela referência imutável. Há retorno seguro (`release`) que devolve a
autoridade à origem anterior.
Nada aqui roda sozinho: não há chamada automática, só a CLI (`license-adopt`, `license-release`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.errors import AppError, bad_request, not_found
from fm_seller.licensing.contract import STATUS_FOR_STATE, License
from fm_seller.licensing.service import _lock, _plan_for
from fm_seller.provisioning import lifecycle
from fm_seller.services import audit


def _require_approval(approved_by: str, evidence: str) -> tuple[str, str]:
    who, proof = approved_by.strip(), evidence.strip()
    if not 3 <= len(who) <= 120 or not 8 <= len(proof) <= 300:
        raise bad_request(
            "approval_required",
            "Informe quem aprovou (3 a 120 caracteres) e a evidência (8 a 300 caracteres).",
        )
    return who, proof


def adopt(
    db: Database,
    settings: Settings,
    *,
    tenant_id: uuid.UUID,
    lic: License,
    approved_by: str,
    evidence: str,
    now: datetime | None = None,
) -> str:
    who, proof = _require_approval(approved_by, evidence)
    if settings.fmcommand_mode != "enforce":
        raise AppError(409, "fmcommand_not_enforcing", "A virada só vale com o modo enforce.")
    if lic.product_code != settings.fmcommand_product_code:
        raise AppError(422, "unsupported_product", "Produto desconhecido.")
    if lic.state not in STATUS_FOR_STATE:
        raise AppError(409, "license_not_applicable", "A licença ainda não está ativa.")
    stamp = now or datetime.now(UTC)
    with db.tx(system=True) as conn:
        _lock(conn, lic.license_id)
        plan = conn.execute(
            "SELECT source, external_ref, status, plan_key FROM tenant_plans "
            "WHERE tenant_id = %s FOR UPDATE",
            (tenant_id,),
        ).fetchone()
        if plan is None:
            raise not_found("Cliente sem plano para assumir.")
        if plan["source"] == "fmcommand":
            raise AppError(409, "already_fmcommand", "Este cliente já é governado pelo Command.")
        clash = conn.execute(
            "SELECT 1 FROM commercial_links WHERE tenant_id = %s OR license_id = %s "
            "OR subscription_id = %s",
            (tenant_id, lic.license_id, lic.subscription_id),
        ).fetchone()
        if clash is not None:
            raise AppError(409, "link_conflict", "Licença ou cliente já estão vinculados.")
        conn.execute(
            "INSERT INTO commercial_links (tenant_id, product_code, customer_id, subscription_id, "
            "license_id, authority, previous_source, previous_external_ref, license_version, "
            "state, plan_code, valid_from, valid_until, grace_ends_at, last_validated_at, "
            "authority_changed_at, authority_approved_by, authority_evidence) "
            "VALUES (%s, %s, %s, %s, %s, 'fmcommand', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s)",
            (
                tenant_id,
                lic.product_code,
                lic.customer_id,
                lic.subscription_id,
                lic.license_id,
                plan["source"],
                plan["external_ref"],
                lic.version,
                lic.state,
                lic.plan_code,
                lic.valid_from,
                lic.valid_until,
                lic.grace_ends_at,
                stamp,
                stamp,
                who,
                proof,
            ),
        )
        conn.execute(
            "UPDATE tenant_plans SET source = 'fmcommand', external_ref = %s WHERE tenant_id = %s",
            (str(lic.license_id), tenant_id),
        )
        new_status = STATUS_FOR_STATE[lic.state]
        if plan["status"] != new_status:
            lifecycle.set_status(conn, tenant_id, new_status, target=str(lic.license_id), now=stamp)
        mapped = _plan_for(conn, lic.plan_code)
        if mapped is not None and mapped != plan["plan_key"]:
            conn.execute(
                "UPDATE tenant_plans SET plan_key = %s WHERE tenant_id = %s", (mapped, tenant_id)
            )
        audit(
            conn,
            tenant_id=tenant_id,
            actor=None,
            action="license.authority_transferred",
            target=str(lic.license_id),
            detail={
                "approved_by": who,
                "evidence": proof,
                "previous_source": plan["source"],
                "version": lic.version,
            },
        )
    return "adopted"


def release(
    db: Database,
    settings: Settings,
    *,
    tenant_id: uuid.UUID,
    approved_by: str,
    evidence: str,
    now: datetime | None = None,
) -> str:
    """Retorno seguro: devolve a autoridade à origem anterior (Cakto/Hotmart), sem apagar nada."""
    who, proof = _require_approval(approved_by, evidence)
    stamp = now or datetime.now(UTC)
    with db.tx(system=True) as conn:
        link = conn.execute(
            "SELECT * FROM commercial_links WHERE tenant_id = %s FOR UPDATE", (tenant_id,)
        ).fetchone()
        if link is None or link["authority"] != "fmcommand":
            raise not_found("Este cliente não é governado pelo Command.")
        if link["previous_source"] is None:
            raise AppError(
                409, "no_previous_authority", "Conta criada pelo Command: não há origem anterior."
            )
        conn.execute(
            "UPDATE tenant_plans SET source = %s, external_ref = %s WHERE tenant_id = %s",
            (link["previous_source"], link["previous_external_ref"], tenant_id),
        )
        conn.execute(
            "UPDATE commercial_links SET authority = 'direct', authority_changed_at = %s, "
            "authority_approved_by = %s, authority_evidence = %s, updated_at = %s "
            "WHERE tenant_id = %s",
            (stamp, who, proof, stamp, tenant_id),
        )
        audit(
            conn,
            tenant_id=tenant_id,
            actor=None,
            action="license.authority_released",
            target=str(link["license_id"]),
            detail={"approved_by": who, "evidence": proof, "to": link["previous_source"]},
        )
    return "released"
