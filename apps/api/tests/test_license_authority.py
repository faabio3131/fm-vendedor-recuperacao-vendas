"""Troca de autoridade de uma assinatura: só com aprovação humana registrada, sem cobrança nova."""

from __future__ import annotations

from typing import Any

import pytest

from fm_seller.cli import map_product
from fm_seller.db import Database
from fm_seller.errors import AppError
from fm_seller.licensing import authority
from fm_seller.licensing.contract import parse_license
from tests.conftest import Env, unique_email
from tests.license_helpers import NOW, Lic, license_client, post_event, settings_for, uuid7

WHO = "Fábio (Diretor)"
PROOF = "Relatório de sombra de 08/10/2026, sem divergência"


@pytest.fixture(autouse=True)
def plans(env: Env) -> None:
    map_product(env.admin_url, "fmcommand", "plano-teste", "fase-2")


def direct_tenant(env: Env, gateway: str = "cakto", ref: str | None = None) -> Any:
    tid = env.tenant("Loja Direta", unique_email("direta"), plan="fase-1")
    env.sql(
        "UPDATE tenant_plans SET source = %s, external_ref = %s WHERE tenant_id = %s",
        (gateway, ref or f"ref-{uuid7()}", tid),
    )
    return tid


def tenants(env: Env) -> int:
    return int(env.sql("SELECT count(*) FROM tenants")[0][0])


def adopt(env: Env, db: Database, tid: Any, lic: Lic, **kw: Any) -> str:
    return authority.adopt(
        db,
        kw.pop("cfg", settings_for(env)),
        tenant_id=tid,
        lic=parse_license(lic.item()),
        approved_by=kw.pop("approved_by", WHO),
        evidence=kw.pop("evidence", PROOF),
        now=NOW,
    )


def test_adoption_links_the_existing_tenant_without_creating_account_or_charge(
    env: Env, db: Database
) -> None:
    tid = direct_tenant(env, "hotmart", "hm-123")
    lic = Lic(plan_code="plano-teste")
    before = tenants(env)
    invites = env.sql("SELECT count(*) FROM pending_invites WHERE tenant_id = %s", (tid,))
    assert adopt(env, db, tid, lic) == "adopted"
    assert tenants(env) == before  # nenhuma conta nova
    assert env.sql("SELECT count(*) FROM pending_invites WHERE tenant_id = %s", (tid,)) == invites
    link = env.sql(
        "SELECT authority, previous_source, previous_external_ref, authority_approved_by, "
        "authority_evidence, license_version FROM commercial_links WHERE tenant_id = %s",
        (tid,),
    )
    assert link == [("fmcommand", "hotmart", "hm-123", WHO, PROOF, 1)]
    plan = env.sql(
        "SELECT source, external_ref, plan_key, status FROM tenant_plans WHERE tenant_id = %s",
        (tid,),
    )
    assert plan == [("fmcommand", lic.license_id, "fase-2", "active")]
    audit = env.sql(
        "SELECT detail FROM audit_log WHERE tenant_id = %s "
        "AND action = 'license.authority_transferred'",
        (tid,),
    )
    assert audit[0][0]["approved_by"] == WHO and audit[0][0]["previous_source"] == "hotmart"


def test_adoption_applies_the_command_state_and_later_events_are_authoritative(
    env: Env, db: Database
) -> None:
    tid = direct_tenant(env)
    lic = Lic(state="suspended")
    adopt(env, db, tid, lic)
    assert env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (tid,)) == [
        ("suspended",)
    ]
    with license_client(env, db) as c:
        res = post_event(c, lic.at(version=2, state="active").event("license.recovered"))
    assert res.json() == {"status": "applied"}
    assert env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (tid,)) == [("active",)]


@pytest.mark.parametrize(
    ("who", "proof"),
    [
        ("", PROOF),
        ("  ", PROOF),
        ("ab", PROOF),
        (WHO, ""),
        (WHO, "curto"),
        ("x" * 121, PROOF),
        (WHO, "e" * 301),
    ],
)
def test_human_approval_is_mandatory_and_recorded(
    env: Env, db: Database, who: str, proof: str
) -> None:
    tid = direct_tenant(env)
    with pytest.raises(AppError) as err:
        adopt(env, db, tid, Lic(), approved_by=who, evidence=proof)
    assert err.value.code == "approval_required"
    assert env.sql("SELECT count(*) FROM commercial_links WHERE tenant_id = %s", (tid,)) == [(0,)]


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_adoption_only_in_enforce_mode(env: Env, db: Database, mode: str) -> None:
    tid = direct_tenant(env)
    cfg = settings_for(env, fmcommand_mode=mode)
    with pytest.raises(AppError) as err:
        adopt(env, db, tid, Lic(), cfg=cfg)
    assert err.value.code == "fmcommand_not_enforcing"
    assert env.sql("SELECT source FROM tenant_plans WHERE tenant_id = %s", (tid,)) == [("cakto",)]


def test_adoption_refuses_other_products_pending_licenses_and_conflicts(
    env: Env, db: Database
) -> None:
    tid = direct_tenant(env)
    with pytest.raises(AppError) as err:
        adopt(env, db, tid, Lic(product="KORDENA"))
    assert err.value.code == "unsupported_product"
    with pytest.raises(AppError) as err:
        adopt(env, db, tid, Lic(state="pending"))
    assert err.value.code == "license_not_applicable"
    lic = Lic()
    assert adopt(env, db, tid, lic) == "adopted"
    with pytest.raises(AppError) as err:  # o mesmo cliente de novo
        adopt(env, db, tid, Lic())
    assert err.value.code == "already_fmcommand"
    other = direct_tenant(env)
    with pytest.raises(AppError) as err:  # licença que já pertence a outro cliente
        adopt(env, db, other, lic)
    assert err.value.code == "link_conflict"
    assert env.sql("SELECT source FROM tenant_plans WHERE tenant_id = %s", (other,)) == [("cakto",)]


def test_unknown_tenant_cannot_be_adopted(env: Env, db: Database) -> None:
    with pytest.raises(AppError) as err:
        adopt(env, db, uuid7(), Lic())
    assert err.value.status == 404


def test_release_gives_authority_back_to_the_previous_source(env: Env, db: Database) -> None:
    tid = direct_tenant(env, "cakto", "ck-777")
    lic = Lic()
    adopt(env, db, tid, lic)
    cfg = settings_for(env)
    with pytest.raises(AppError) as err:
        authority.release(db, cfg, tenant_id=tid, approved_by="", evidence="")
    assert err.value.code == "approval_required"
    assert authority.release(db, cfg, tenant_id=tid, approved_by=WHO, evidence=PROOF) == "released"
    assert env.sql(
        "SELECT source, external_ref FROM tenant_plans WHERE tenant_id = %s", (tid,)
    ) == [("cakto", "ck-777")]
    assert env.sql("SELECT authority FROM commercial_links WHERE tenant_id = %s", (tid,)) == [
        ("direct",)
    ]
    with license_client(env, db) as c:  # depois do retorno o Command deixa de governar
        res = post_event(c, lic.at(version=2, state="canceled").event("license.canceled"))
    assert res.json() == {"status": "not_authoritative"}
    assert env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (tid,)) == [("active",)]
    actions = env.sql("SELECT action FROM audit_log WHERE tenant_id = %s ORDER BY id", (tid,))
    assert ("license.authority_released",) in actions


def test_account_created_by_the_command_has_no_previous_authority_to_return_to(
    env: Env, db: Database
) -> None:
    lic = Lic()
    with license_client(env, db) as c:
        assert post_event(c, lic.event()).json() == {"status": "provisioned"}
    tid = env.sql(
        "SELECT tenant_id FROM commercial_links WHERE license_id = %s", (lic.license_id,)
    )[0][0]
    with pytest.raises(AppError) as err:
        authority.release(db, settings_for(env), tenant_id=tid, approved_by=WHO, evidence=PROOF)
    assert err.value.code == "no_previous_authority"


def test_release_of_a_tenant_not_governed_by_the_command_is_refused(env: Env, db: Database) -> None:
    with pytest.raises(AppError) as err:
        authority.release(
            db, settings_for(env), tenant_id=direct_tenant(env), approved_by=WHO, evidence=PROOF
        )
    assert err.value.status == 404
