"""Contingência limitada: uma janela de no máximo 72 h por versão, persistida e sem renovação."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from fm_seller.cli import map_product
from fm_seller.db import Database
from fm_seller.licensing import contingency as cont
from fm_seller.licensing import service
from fm_seller.licensing.contract import parse_license
from tests.conftest import Env
from tests.license_helpers import NOW, Lic, license_client, post_event, settings_for

H = timedelta(hours=1)
CAP = timedelta(hours=72)


@pytest.fixture(autouse=True)
def plans(env: Env) -> None:
    map_product(env.admin_url, "fmcommand", "plano-teste", "fase-2")


def link_row(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "state": "active",
        "valid_until": NOW,
        "grace_ends_at": None,
        "license_version": 1,
        "contingency_version": None,
        "contingency_started_at": None,
        "contingency_until": None,
        "contingency_exhausted_at": None,
    }
    base.update(kw)
    return base


# ------------------------------------------------------------------ decisão pura


def test_before_the_end_everything_is_normal() -> None:
    assert cont.decide(link_row(), NOW - H, CAP).action == "ok"
    assert cont.decide(link_row(), NOW, CAP).action == "ok"  # no instante exato ainda vale


def test_window_starts_at_the_end_of_validity_not_when_it_was_noticed() -> None:
    d = cont.decide(link_row(), NOW + 10 * H, CAP)
    assert d.action == "start" and d.started_at == NOW and d.until == NOW + CAP


def test_window_is_served_until_72_hours_then_exhausted() -> None:
    row = link_row(contingency_version=1, contingency_started_at=NOW, contingency_until=NOW + CAP)
    assert cont.decide(row, NOW + CAP, CAP).action == "serve"
    assert cont.decide(row, NOW + CAP + timedelta(seconds=1), CAP).action == "exhaust"


def test_late_detection_after_the_window_exhausts_immediately() -> None:
    assert cont.decide(link_row(), NOW + timedelta(days=5), CAP).action == "exhaust"


def test_an_exhausted_window_is_never_renewed_for_the_same_version() -> None:
    row = link_row(
        contingency_version=1,
        contingency_started_at=NOW,
        contingency_until=NOW + CAP,
        contingency_exhausted_at=NOW + CAP,
    )
    assert cont.decide(row, NOW + timedelta(days=30), CAP).action == "skip"


def test_a_new_version_gets_its_own_window() -> None:
    row = link_row(
        license_version=2,
        contingency_version=1,
        contingency_started_at=NOW,
        contingency_until=NOW + CAP,
        contingency_exhausted_at=NOW + CAP,
    )
    assert cont.decide(row, NOW + H, CAP).action == "start"


def test_past_due_uses_the_end_of_the_grace_period() -> None:
    row = link_row(state="past_due", valid_until=NOW, grace_ends_at=NOW + timedelta(days=3))
    assert cont.decide(row, NOW + timedelta(days=2), CAP).action == "ok"
    d = cont.decide(row, NOW + timedelta(days=3, hours=1), CAP)
    assert d.action == "start" and d.started_at == NOW + timedelta(days=3)


@pytest.mark.parametrize("state", ["suspended", "canceled", "refunded", "expired", "pending"])
def test_states_that_already_block_have_nothing_to_decide(state: str) -> None:
    assert cont.decide(link_row(state=state), NOW + 100 * H, CAP).action == "skip"


def test_configured_window_is_honored_and_never_above_72_hours() -> None:
    assert cont.decide(link_row(), NOW + H, timedelta(hours=24)).until == NOW + timedelta(hours=24)
    assert cont.decide(link_row(), NOW + H, timedelta(hours=500)).until == NOW + CAP


def test_both_sides_decide_the_same_as_the_command_domain_function() -> None:
    """Paridade com `mayServeDuringOutage` (PR #62 do Command) em uma grade de situações."""
    for state, grace in (("active", None), ("past_due", NOW + timedelta(days=2))):
        for offset in (-30 * H, -1 * H, 0 * H, 1 * H, 50 * H, 71 * H, 72 * H, 73 * H, 200 * H):
            for window in (None, "open"):
                end = grace or NOW
                started = end if window else None
                until = end + CAP if window else None
                now = end + offset
                row = link_row(
                    state=state,
                    grace_ends_at=grace,
                    contingency_version=1 if window else None,
                    contingency_started_at=started,
                    contingency_until=until,
                )
                d = cont.decide(row, now, CAP)
                # Quem cuida da janela é o worker: aqui a janela só vale se já foi aberta.
                serves = d.action in ("ok", "serve") or (
                    d.action == "start" and now <= (d.until or now)
                )
                mine = (window is not None and serves) or d.action == "ok"
                theirs = cont.may_serve_during_outage(
                    state=state,
                    valid_until=NOW,
                    grace_ends_at=grace,
                    now=now,
                    last_validated_at=NOW - 100 * H,
                    contingency_started_at=started,
                    contingency_until=until,
                )
                assert mine == theirs, (state, offset, window, d)


def test_parity_function_fails_closed_on_any_doubt() -> None:
    kw: dict[str, Any] = {
        "state": "active",
        "valid_until": NOW,
        "grace_ends_at": None,
        "now": NOW + 2 * H,
        "last_validated_at": NOW - H,
        "contingency_started_at": NOW,
        "contingency_until": NOW + CAP,
    }
    assert cont.may_serve_during_outage(**kw)
    assert not cont.may_serve_during_outage(**{**kw, "last_validated_at": NOW + 5 * H})
    assert not cont.may_serve_during_outage(**{**kw, "contingency_started_at": None})
    assert not cont.may_serve_during_outage(**{**kw, "contingency_until": NOW + CAP + H})
    assert not cont.may_serve_during_outage(**{**kw, "contingency_started_at": NOW - H})
    assert not cont.may_serve_during_outage(**{**kw, "state": "suspended"})


# ------------------------------------------------------------------ com banco


def provisioned(env: Env, db: Database, **kw: Any) -> tuple[Lic, Any]:
    lic = Lic(valid_until=NOW, valid_from=NOW - timedelta(days=30), **kw)
    with license_client(env, db) as c:
        assert post_event(c, lic.event()).json() == {"status": "provisioned"}
    tid = env.sql(
        "SELECT tenant_id FROM commercial_links WHERE license_id = %s", (lic.license_id,)
    )[0][0]
    return lic, tid


def fields(env: Env, tid: Any) -> tuple[Any, ...]:
    return env.sql(
        "SELECT contingency_version, contingency_started_at, contingency_until, "
        "contingency_exhausted_at FROM commercial_links WHERE tenant_id = %s",
        (tid,),
    )[0]


def status(env: Env, tid: Any) -> str:
    return str(env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (tid,))[0][0])


def actions(env: Env, tid: Any) -> list[str]:
    rows = env.sql(
        "SELECT action FROM audit_log WHERE tenant_id = %s AND action LIKE 'license.contingency%%' "
        "ORDER BY id",
        (tid,),
    )
    return [r[0] for r in rows]


def test_full_life_of_a_contingency_window(env: Env, db: Database) -> None:
    lic, tid = provisioned(env, db)
    cfg = settings_for(env)
    cont.enforce_licenses(db, cfg, now=NOW - H)
    assert fields(env, tid) == (None, None, None, None)

    # Notado 10 h depois do fim: a janela começa no FIM da vigência (persistida) e o serviço segue.
    cont.enforce_licenses(db, cfg, now=NOW + 10 * H)
    version, started, until, exhausted = fields(env, tid)
    assert (version, started, until, exhausted) == (1, NOW, NOW + CAP, None)
    assert status(env, tid) == "active"

    # Rodar de novo não estende nem recria.
    cont.enforce_licenses(db, cfg, now=NOW + 20 * H)
    assert fields(env, tid)[1:3] == (NOW, NOW + CAP)

    # Acabou a janela: suspende, sem apagar nada.
    cont.enforce_licenses(db, cfg, now=NOW + CAP + H)
    assert status(env, tid) == "suspended"
    assert fields(env, tid)[3] is not None

    # Não renova sozinha, nem depois de muito tempo.
    cont.enforce_licenses(db, cfg, now=NOW + timedelta(days=60))
    assert status(env, tid) == "suspended"
    assert actions(env, tid) == ["license.contingency_started", "license.contingency_exhausted"]
    assert lic.license_id  # a licença segue a mesma


def test_a_validated_newer_license_reopens_and_clears_the_window(env: Env, db: Database) -> None:
    lic, tid = provisioned(env, db)
    cfg = settings_for(env)
    cont.enforce_licenses(db, cfg, now=NOW + 10 * H)
    assert fields(env, tid)[0] == 1
    renewed = lic.at(version=2, valid_until=NOW + timedelta(days=30), valid_from=NOW)
    with license_client(env, db) as c:
        assert post_event(c, renewed.event("license.renewed")).json() == {"status": "applied"}
    assert fields(env, tid) == (None, None, None, None)
    assert status(env, tid) == "active"
    cont.enforce_licenses(db, cfg, now=NOW + 11 * H)
    assert fields(env, tid) == (None, None, None, None)  # versão nova, vigência nova: sem janela


def test_reconfirming_the_same_version_does_not_renew_the_window(env: Env, db: Database) -> None:
    lic, tid = provisioned(env, db)
    cfg = settings_for(env)
    cont.enforce_licenses(db, cfg, now=NOW + 10 * H)
    before = fields(env, tid)
    with db.tx(system=True) as conn:
        res = service.apply_license(
            conn,
            parse_license(lic.item()),
            None,
            None,
            channel="reconciliation",
            now=NOW + 11 * H,
        )
    assert res.outcome == "validated"
    assert fields(env, tid) == before


def test_window_never_opens_for_a_late_detection_longer_than_the_window(
    env: Env, db: Database
) -> None:
    _, tid = provisioned(env, db)
    cont.enforce_licenses(db, settings_for(env), now=NOW + timedelta(days=9))
    assert status(env, tid) == "suspended"
    assert fields(env, tid)[1:3] == (
        NOW,
        NOW + CAP,
    )  # a janela é a do fim da vigência, não a de hoje


def test_configured_cap_is_applied(env: Env, db: Database) -> None:
    _, tid = provisioned(env, db)
    cont.enforce_licenses(db, settings_for(env, fmcommand_contingency_hours=24), now=NOW + H)
    assert fields(env, tid)[2] == NOW + timedelta(hours=24)


def test_only_enforce_mode_acts_and_direct_authority_is_never_touched(
    env: Env, db: Database
) -> None:
    _, tid = provisioned(env, db)
    out = cont.enforce_licenses(db, settings_for(env, fmcommand_mode="shadow"), now=NOW + 200 * H)
    assert out == {"iniciadas": 0, "encerradas": 0} and fields(env, tid) == (None, None, None, None)
    env.sql("UPDATE commercial_links SET authority = 'direct' WHERE tenant_id = %s", (tid,))
    cont.enforce_licenses(db, settings_for(env), now=NOW + 200 * H)
    assert fields(env, tid) == (None, None, None, None) and status(env, tid) == "active"


def test_past_due_license_gets_its_window_after_the_grace_period(env: Env, db: Database) -> None:
    grace = NOW + timedelta(days=3)
    lic = Lic(
        valid_until=NOW, valid_from=NOW - timedelta(days=30), state="past_due", grace_ends_at=grace
    )
    with license_client(env, db) as c:
        assert post_event(c, lic.event("license.past_due")).json() == {"status": "provisioned"}
    tid = env.sql(
        "SELECT tenant_id FROM commercial_links WHERE license_id = %s", (lic.license_id,)
    )[0][0]
    cfg = settings_for(env)
    assert cont.enforce_licenses(db, cfg, now=NOW + timedelta(days=2)) == {
        "iniciadas": 0,
        "encerradas": 0,
    }
    assert cont.enforce_licenses(db, cfg, now=grace + H)["iniciadas"] == 1
    assert fields(env, tid)[1] == grace
    _ = datetime  # o relógio é sempre injetado
