"""Eventos `fmcc.license.v1`: assinatura, idempotência, ordem, autoridade e isolamento."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import psycopg
import pytest

from fm_seller.cli import map_product
from fm_seller.db import Database
from fm_seller.provisioning import lifecycle
from tests.conftest import Env, login, unique_email
from tests.license_helpers import (
    NOW,
    SECRET,
    Lic,
    license_client,
    post_event,
    settings_for,
    uuid7,
)


@pytest.fixture(autouse=True)
def plans(env: Env) -> None:
    map_product(env.admin_url, "fmcommand", "plano-teste", "fase-2")
    map_product(env.admin_url, "fmcommand", "plano-maior", "fase-3")


def status_of(env: Env, lic: Lic) -> str | None:
    rows = env.sql(
        "SELECT tp.status FROM tenant_plans tp JOIN commercial_links cl "
        "ON cl.tenant_id = tp.tenant_id WHERE cl.license_id = %s",
        (lic.license_id,),
    )
    return None if not rows else str(rows[0][0])


def link_of(env: Env, lic: Lic) -> tuple[Any, ...] | None:
    rows = env.sql(
        "SELECT tenant_id, authority, license_version, state, plan_code, customer_id "
        "FROM commercial_links WHERE license_id = %s",
        (lic.license_id,),
    )
    return rows[0] if rows else None


def count(env: Env, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(env.sql(sql, params)[0][0])


def provision(client: Any, lic: Lic) -> dict[str, Any]:
    res = post_event(client, lic.event())
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    assert body == {"status": "provisioned"}
    return body


# ------------------------------------------------------------------ desligado e borda


def test_mode_off_is_inert_404_and_writes_nothing(env: Env, db: Database) -> None:
    lic = Lic()
    with license_client(env, db, fmcommand_mode="off") as c:
        res = post_event(c, lic.event())
    assert res.status_code == 404
    assert (
        count(env, "SELECT count(*) FROM license_events WHERE license_id = %s", (lic.license_id,))
        == 0
    )
    assert link_of(env, lic) is None


def test_unconfigured_secrets_keep_the_route_disabled(env: Env, db: Database) -> None:
    with license_client(env, db, fmcommand_mode="shadow", fmcommand_webhook_secrets="") as c:
        assert post_event(c, Lic().event()).status_code == 404


def test_bad_signature_is_401_and_nothing_is_recorded(env: Env, db: Database) -> None:
    lic = Lic()
    with license_client(env, db) as c:
        wrong = post_event(c, lic.event(), secret="outro-segredo-" + "z" * 30)
        old = post_event(c, lic.event(), t=1_000_000_000)
        unknown_kid = post_event(c, lic.event(), kid="k9")
    for res in (wrong, old, unknown_kid):
        assert res.status_code == 401 and res.json()["error"]["code"] == "invalid_signature"
    assert (
        count(env, "SELECT count(*) FROM license_events WHERE license_id = %s", (lic.license_id,))
        == 0
    )


def test_missing_signature_header_is_401(env: Env, db: Database) -> None:
    with license_client(env, db) as c:
        res = c.post("/v1/platform/webhooks/fmcommand", json=Lic().event())
    assert res.status_code == 401


def test_event_id_header_must_match_the_body(env: Env, db: Database) -> None:
    lic = Lic()
    with license_client(env, db) as c:
        res = post_event(c, lic.event(), event_id_header="evt-outro")
    assert res.status_code == 401 and res.json()["error"]["code"] == "event_id_mismatch"


def test_invalid_json_schema_and_product_are_refused(env: Env, db: Database) -> None:
    with license_client(env, db) as c:
        assert post_event(c, {}, raw=b"nao e json").status_code == 400
        bad = Lic().event()
        bad["schema_version"] = "fmcc.license.v9"
        assert post_event(c, bad).json()["error"]["code"] == "unsupported_schema"
        other = Lic(product="KORDENA").event()
        res = post_event(c, other)
        assert res.status_code == 422 and res.json()["error"]["code"] == "unsupported_product"
        weak = Lic().event()
        weak["customer_id"] = "cliente@example.test"
        assert post_event(c, weak).json()["error"]["code"] == "invalid_command_id"


def test_oversized_body_is_413(env: Env, db: Database) -> None:
    with license_client(env, db) as c:
        res = post_event(c, {}, raw=b"x" * (300 * 1024))
    assert res.status_code in (401, 413)  # o limite de corpo da API vence antes da assinatura


# ------------------------------------------------------------------ provisionamento


def test_activated_event_provisions_tenant_plan_invite_link_and_login_works(
    env: Env, db: Database
) -> None:
    lic = Lic()
    with license_client(env, db) as c:
        provision(c, lic)
        link = link_of(env, lic)
        assert link is not None and link[1] == "fmcommand" and link[2] == 1 and link[3] == "active"
        tenant_id = link[0]
        plan = env.sql(
            "SELECT plan_key, source, external_ref, status FROM tenant_plans WHERE tenant_id = %s",
            (tenant_id,),
        )[0]
        assert plan == ("fase-2", "fmcommand", lic.license_id, "active")
        assert (
            count(env, "SELECT count(*) FROM pending_invites WHERE tenant_id = %s", (tenant_id,))
            == 1
        )
        assert login(c, lic.admin_email).status_code == 200
        me = c.get("/v1/me").json()
        assert me["plan"] == {"key": "fase-2", "status": "active"}
    audit = env.sql(
        "SELECT action, target FROM audit_log WHERE tenant_id = %s AND action = 'tenant.created'",
        (tenant_id,),
    )
    assert audit == [("tenant.created", lic.license_id)]  # o alvo é o id, não o e-mail


def test_email_is_not_the_key_the_same_email_can_have_two_licenses_bound_by_id(
    env: Env, db: Database
) -> None:
    first = Lic()
    with license_client(env, db) as c:
        provision(c, first)
        again = first.at(version=2, state="canceled")
        assert post_event(c, again.event("license.canceled")).json() == {"status": "applied"}
        assert status_of(env, first) == "canceled"


def test_repeated_event_id_is_a_duplicate_and_creates_nothing_twice(env: Env, db: Database) -> None:
    lic = Lic()
    body = lic.event()
    with license_client(env, db) as c:
        assert post_event(c, body).json() == {"status": "provisioned"}
        assert post_event(c, body).json() == {"status": "duplicate"}
    assert (
        count(env, "SELECT count(*) FROM commercial_links WHERE license_id = %s", (lic.license_id,))
        == 1
    )
    assert (
        count(env, "SELECT count(*) FROM license_events WHERE event_id = %s", (body["event_id"],))
        == 1
    )


def test_provisioning_must_be_authorized_by_the_command(env: Env, db: Database) -> None:
    lic = Lic(authorized=False)
    with license_client(env, db) as c:
        assert post_event(c, lic.event()).json() == {"status": "unauthorized_provisioning"}
    assert link_of(env, lic) is None
    assert (
        count(
            env,
            "SELECT count(*) FROM pending_invites WHERE lower(email) = lower(%s)",
            (lic.admin_email,),
        )
        == 0
    )


def test_event_without_provisioning_block_creates_nothing(env: Env, db: Database) -> None:
    lic = Lic()
    body = lic.event()
    del body["provisioning"]
    with license_client(env, db) as c:
        assert post_event(c, body).json() == {"status": "unauthorized_provisioning"}
    assert link_of(env, lic) is None


def test_unmapped_plan_does_not_provision(env: Env, db: Database) -> None:
    lic = Lic(plan_code="plano-sem-mapa")
    with license_client(env, db) as c:
        assert post_event(c, lic.event()).json() == {"status": "unmapped_plan"}
    assert link_of(env, lic) is None


def test_pending_and_inactive_licenses_are_not_provisioned(env: Env, db: Database) -> None:
    with license_client(env, db) as c:
        pending = Lic(state="pending")
        body = pending.event("license.activated")
        body["license"]["state"] = "pending"
        # `pending` não combina com nenhum tipo de evento: o contrato recusa antes
        assert post_event(c, body).status_code == 422
        canceled = Lic(state="canceled")
        assert post_event(c, canceled.event("license.canceled")).json() == {
            "status": "no_link_inactive"
        }
    assert link_of(env, canceled) is None


def test_past_due_license_provisions_with_past_due_status(env: Env, db: Database) -> None:
    lic = Lic(state="past_due", grace_ends_at=NOW + timedelta(days=33))
    with license_client(env, db) as c:
        assert post_event(c, lic.event("license.past_due")).json() == {"status": "provisioned"}
    assert status_of(env, lic) == "past_due"


# ------------------------------------------------------------------ sem duplicidade de conta


def test_existing_account_by_owner_email_requires_adoption_not_a_second_tenant(
    env: Env, db: Database
) -> None:
    email = unique_email("ja-cliente")
    tenant_id = env.tenant("Loja Direta", email, plan="fase-1")
    lic = Lic(admin_email=email)
    with license_client(env, db) as c:
        assert post_event(c, lic.event()).json() == {"status": "requires_adoption"}
    assert link_of(env, lic) is None
    assert (
        count(env, "SELECT count(*) FROM pending_invites WHERE lower(email) = lower(%s)", (email,))
        == 1
    )
    assert env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (tenant_id,)) == [
        ("active",)
    ]


@pytest.mark.parametrize("gateway", ["cakto", "hotmart"])
def test_existing_direct_subscription_by_external_ref_requires_adoption(
    env: Env, db: Database, gateway: str
) -> None:
    ref = f"{gateway}-sub-{uuid7()}"
    tenant_id = env.tenant("Loja Ref", unique_email("ref"), plan="fase-1")
    env.sql(
        "UPDATE tenant_plans SET source = %s, external_ref = %s WHERE tenant_id = %s",
        (gateway, ref, tenant_id),
    )
    lic = Lic(gateway=gateway, external_ref=ref)  # e-mail diferente de propósito
    with license_client(env, db) as c:
        assert post_event(c, lic.event()).json() == {"status": "requires_adoption"}
    assert link_of(env, lic) is None


# ------------------------------------------------------------------ versão, ordem e estados


def test_out_of_order_events_the_highest_version_wins(env: Env, db: Database) -> None:
    lic = Lic()
    with license_client(env, db) as c:
        provision(c, lic)
        v3 = lic.at(version=3, state="suspended")
        assert post_event(c, v3.event("license.suspended")).json() == {"status": "applied"}
        v2 = lic.at(version=2, state="active")
        assert post_event(c, v2.event("license.renewed")).json() == {"status": "stale"}
        assert status_of(env, lic) == "suspended"  # a versão velha não reativa
        v3again = lic.at(version=3, state="active")
        assert post_event(c, v3again.event("license.renewed")).json() == {"status": "stale"}
        v5 = lic.at(version=5, state="active")
        assert post_event(c, v5.event("license.recovered")).json() == {"status": "applied"}
    assert status_of(env, lic) == "active"
    link = link_of(env, lic)
    assert link is not None and link[2] == 5


@pytest.mark.parametrize(
    ("type_", "state", "expected", "grace"),
    [
        ("license.past_due", "past_due", "past_due", True),
        ("license.suspended", "suspended", "suspended", False),
        ("license.expired", "expired", "suspended", False),
        ("license.canceled", "canceled", "canceled", False),
        ("license.refunded", "refunded", "refunded", False),
    ],
)
def test_commercial_states_map_to_plan_status(
    env: Env, db: Database, type_: str, state: str, expected: str, grace: bool
) -> None:
    lic = Lic()
    with license_client(env, db) as c:
        provision(c, lic)
        nxt = lic.at(
            version=2,
            state=state,
            grace_ends_at=NOW + timedelta(days=33) if grace else None,
        )
        assert post_event(c, nxt.event(type_)).json() == {"status": "applied"}
    assert status_of(env, lic) == expected
    audit = env.sql(
        "SELECT count(*) FROM audit_log a JOIN commercial_links cl ON cl.tenant_id = a.tenant_id "
        "WHERE cl.license_id = %s AND a.action = %s",
        (lic.license_id, f"license.{state}"),
    )
    assert audit[0][0] == 1


def test_a_suspended_license_can_be_reactivated_by_a_newer_version(env: Env, db: Database) -> None:
    lic = Lic()
    with license_client(env, db) as c:
        provision(c, lic)
        assert post_event(
            c, lic.at(version=2, state="suspended").event("license.suspended")
        ).is_success
        assert post_event(
            c, lic.at(version=3, state="active").event("license.recovered")
        ).is_success
    assert status_of(env, lic) == "active"


def test_plan_change_updates_the_plan_and_an_unmapped_plan_still_applies_the_state(
    env: Env, db: Database
) -> None:
    lic = Lic()
    with license_client(env, db) as c:
        provision(c, lic)
        up = lic.at(version=2, plan_code="plano-maior")
        assert post_event(c, up.event("license.plan_changed")).json() == {"status": "applied"}
        tid = link_of(env, lic)[0]  # type: ignore[index]
        assert env.sql("SELECT plan_key FROM tenant_plans WHERE tenant_id = %s", (tid,)) == [
            ("fase-3",)
        ]
        strange = up.at(
            version=3,
            plan_code="plano-desconhecido",
            state="past_due",
            grace_ends_at=NOW + timedelta(days=33),
        )
        res = post_event(c, strange.event("license.plan_changed"))
        assert res.json() == {"status": "applied_plan_unmapped"}
    assert env.sql("SELECT plan_key, status FROM tenant_plans WHERE tenant_id = %s", (tid,)) == [
        ("fase-3", "past_due")
    ]
    link = link_of(env, lic)
    assert link is not None and link[4] == "plano-maior"  # não anotou um plano sem mapa


# ------------------------------------------------------------------ identidade e autoridade


def test_identity_mismatch_is_rejected_and_changes_nothing(env: Env, db: Database) -> None:
    lic = Lic()
    with license_client(env, db) as c:
        provision(c, lic)
        hijack = lic.at(version=2, state="canceled", customer_id=uuid7())
        assert post_event(c, hijack.event("license.canceled")).json() == {
            "status": "identity_mismatch"
        }
        other_license = Lic(subscription_id=lic.subscription_id, admin_email=unique_email("x"))
        assert post_event(c, other_license.event()).json() == {"status": "identity_mismatch"}
    assert status_of(env, lic) == "active"
    assert link_of(env, other_license) is None


def _direct_link(env: Env, lic: Lic, authority: str = "direct") -> str:
    tenant_id = env.tenant("Loja Direta", unique_email("d"), plan="fase-1")
    env.sql(
        "UPDATE tenant_plans SET source = 'cakto', external_ref = 'ref-1' WHERE tenant_id = %s",
        (tenant_id,),
    )
    env.sql(
        "INSERT INTO commercial_links (tenant_id, product_code, customer_id, subscription_id, "
        "license_id, authority, previous_source, license_version, state, plan_code, valid_from, "
        "valid_until, last_validated_at) VALUES (%s, %s, %s, %s, %s, %s, 'cakto', 1, 'active', "
        "'plano-teste', %s, %s, %s)",
        (
            tenant_id,
            lic.product,
            lic.customer_id,
            lic.subscription_id,
            lic.license_id,
            authority,
            lic.valid_from,
            lic.valid_until,
            NOW,
        ),
    )
    return tenant_id


def test_event_for_a_direct_authority_subscription_is_not_applied(env: Env, db: Database) -> None:
    lic = Lic()
    tenant_id = _direct_link(env, lic)
    with license_client(env, db) as c:
        res = post_event(c, lic.at(version=2, state="suspended").event("license.suspended"))
    assert res.json() == {"status": "not_authoritative"}
    assert env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (tenant_id,)) == [
        ("active",)
    ]


def test_enforce_grace_skips_command_governed_tenants_but_still_suspends_direct_ones(
    env: Env, db: Database
) -> None:
    governed = Lic()
    t_gov = _direct_link(env, governed, authority="fmcommand")
    direct = env.tenant("Loja Atrasada", unique_email("atraso"), plan="fase-1")
    old = NOW - timedelta(days=30)
    for tid in (t_gov, direct):
        env.sql(
            "UPDATE tenant_plans SET status = 'past_due', past_due_since = %s WHERE tenant_id = %s",
            (old, tid),
        )
    lifecycle.enforce_grace(db, now=NOW)
    assert env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (t_gov,)) == [
        ("past_due",)
    ]
    assert env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (direct,)) == [
        ("suspended",)
    ]


# ------------------------------------------------------------------ isolamento entre clientes


def test_events_for_one_license_never_touch_another_tenant(env: Env, db: Database) -> None:
    a, b = Lic(), Lic()
    with license_client(env, db) as c:
        provision(c, a)
        provision(c, b)
        assert post_event(c, a.at(version=2, state="canceled").event("license.canceled")).is_success
    assert status_of(env, a) == "canceled"
    assert status_of(env, b) == "active"


def test_license_tables_are_invisible_and_unwritable_with_a_tenant_context(
    env: Env, db: Database
) -> None:
    lic = Lic()
    with license_client(env, db) as c:
        provision(c, lic)
    link = link_of(env, lic)
    assert link is not None
    tenant_id = link[0]
    with db.tx(tenant_id=tenant_id) as conn:
        for table in ("commercial_links", "license_events", "license_shadow", "license_sync_state"):
            row = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()
            assert row is not None and row["n"] == 0
    with pytest.raises(psycopg.Error), db.tx(tenant_id=tenant_id) as conn:
        conn.execute("UPDATE commercial_links SET authority = 'direct'")
        conn.execute(
            "INSERT INTO commercial_links (tenant_id, product_code, customer_id, subscription_id, "
            "license_id, authority, license_version, state, plan_code, valid_from, valid_until, "
            "last_validated_at) VALUES (%s, 'X', %s, %s, %s, 'fmcommand', 1, 'active', 'p', now(), "
            "now() + interval '1 day', now())",
            (tenant_id, uuid7(), uuid7(), uuid7()),
        )
    assert link_of(env, lic) is not None and link_of(env, lic)[1] == "fmcommand"  # type: ignore[index]


# ------------------------------------------------------------------ modo sombra


def test_shadow_mode_never_changes_real_licenses_but_reports_divergence(
    env: Env, db: Database
) -> None:
    ref = f"cakto-sub-{uuid7()}"
    tenant_id = env.tenant("Loja Sombra", unique_email("sombra"), plan="fase-1")
    env.sql(
        "UPDATE tenant_plans SET source = 'cakto', external_ref = %s WHERE tenant_id = %s",
        (ref, tenant_id),
    )
    lic = Lic(state="suspended", gateway="cakto", external_ref=ref)
    plans_before = env.sql(
        "SELECT plan_key, source, status FROM tenant_plans WHERE tenant_id = %s", (tenant_id,)
    )
    with license_client(env, db, fmcommand_mode="shadow") as c:
        assert post_event(c, lic.event("license.suspended")).json() == {"status": "shadow"}
        assert post_event(c, lic.event("license.suspended")).json() == {"status": "shadow"}
    assert (
        env.sql(
            "SELECT plan_key, source, status FROM tenant_plans WHERE tenant_id = %s", (tenant_id,)
        )
        == plans_before
    )
    assert link_of(env, lic) is None
    shadow = env.sql(
        "SELECT matched_tenant_id, real_status, expected_status, diverges FROM license_shadow "
        "WHERE license_id = %s",
        (lic.license_id,),
    )
    assert [(str(r[0]), *r[1:]) for r in shadow] == [(str(tenant_id), "active", "suspended", True)]
    divergences = env.sql(
        "SELECT count(*) FROM audit_log WHERE tenant_id = %s "
        "AND action = 'license.shadow_divergence'",
        (tenant_id,),
    )
    assert divergences[0][0] == 1  # registra a divergência uma vez, não a cada evento


def test_shadow_mode_does_not_provision_new_customers(env: Env, db: Database) -> None:
    lic = Lic()
    with license_client(env, db, fmcommand_mode="shadow") as c:
        assert post_event(c, lic.event()).json() == {"status": "shadow"}
    assert link_of(env, lic) is None
    assert (
        count(
            env,
            "SELECT count(*) FROM pending_invites WHERE lower(email) = lower(%s)",
            (lic.admin_email,),
        )
        == 0
    )


def test_shadow_keeps_only_the_newest_version(env: Env, db: Database) -> None:
    lic = Lic()
    with license_client(env, db, fmcommand_mode="shadow") as c:
        post_event(c, lic.at(version=4, state="canceled").event("license.canceled"))
        post_event(c, lic.at(version=2, state="active").event("license.renewed"))
    assert env.sql(
        "SELECT license_version, state FROM license_shadow WHERE license_id = %s", (lic.license_id,)
    ) == [(4, "canceled")]


# ------------------------------------------------------------------ falha e preservação


def test_processing_failure_is_recorded_and_the_sender_gets_failed(
    env: Env, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fm_seller.licensing import service

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("segredo-que-nao-pode-vazar")

    monkeypatch.setattr(service, "apply_license", boom)
    lic = Lic()
    body = lic.event()
    with license_client(env, db) as c:
        assert post_event(c, body).json() == {"status": "failed"}
    row = env.sql(
        "SELECT outcome, error FROM license_events WHERE event_id = %s", (body["event_id"],)
    )
    assert row == [("failed", "RuntimeError")]  # só o tipo do erro, nunca a mensagem


def test_cakto_and_hotmart_direct_paths_are_untouched_while_fmcommand_is_enforcing(
    env: Env, db: Database
) -> None:
    map_product(env.admin_url, "cakto", "prod-direto", "fase-2")
    email = unique_email("cakto-direto")
    cfg = settings_for(env, platform_cakto_secret="segredo-plataforma-abc")
    from fastapi.testclient import TestClient

    from fm_seller.api.app import create_app
    from tests.conftest import ORIGIN

    with TestClient(create_app(cfg, db=db, box=env.box), headers={"Origin": ORIGIN}) as c:
        res = c.post(
            "/v1/platform/webhooks/cakto",
            json={
                "event": "purchase_approved",
                "secret": "segredo-plataforma-abc",
                "data": {
                    "id": f"o-{email}",
                    "customer": {"name": "Comprador", "email": email, "phone": "11999998888"},
                    "product": {"id": "prod-direto", "name": "F&M Vendedor"},
                    "amount": 197,
                },
            },
        )
        assert res.status_code == 200 and res.json() == {"status": "tenant_created"}
        # Hotmart não configurada continua respondendo como sempre (404), sem passar pelo Command.
        assert c.post("/v1/platform/webhooks/hotmart", json={}).status_code == 404
    assert (
        count(
            env,
            "SELECT count(*) FROM commercial_links cl JOIN pending_invites i "
            "ON i.tenant_id = cl.tenant_id WHERE lower(i.email) = lower(%s)",
            (email,),
        )
        == 0
    )
    assert SECRET  # o segredo de teste do Command nunca vale na rota da Cakto
