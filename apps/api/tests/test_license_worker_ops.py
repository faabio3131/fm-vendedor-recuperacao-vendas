"""Worker, saúde e CLI do Billing Central: desligado por padrão, sem derrubar o ciclo."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pytest

from fm_seller import cli
from fm_seller.cli import map_product
from fm_seller.db import Database
from fm_seller.ops import health
from fm_seller.recovery.senders import SimulatedSender
from tests.conftest import Env
from tests.license_helpers import NOW, Lic, license_client, post_event, settings_for

log = logging.getLogger("test")


@pytest.fixture(autouse=True)
def plans(env: Env) -> None:
    map_product(env.admin_url, "fmcommand", "plano-teste", "fase-2")


def codes(found: list[Any]) -> set[str]:
    return {f.code for f in found}


def test_off_mode_step_does_nothing(env: Env, db: Database) -> None:
    off = settings_for(env, fmcommand_mode="off")
    assert cli._license_step(db, env.box, off, log) == {}
    assert cli._license_step(db, env.box, None, log) == {}


def test_step_failure_never_breaks_the_worker_cycle(
    env: Env, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("command fora do ar")

    monkeypatch.setattr("fm_seller.licensing.reconcile.cycle", boom)
    out = cli._license_step(db, env.box, settings_for(env, fmcommand_mode="shadow"), log)
    assert out == {"erro": "RuntimeError"}


def test_health_has_no_license_findings_when_off(env: Env, db: Database) -> None:
    off = settings_for(env, fmcommand_mode="off")
    found = health.check(db, SimulatedSender(), now=NOW, require_worker=False, settings=off)
    assert not codes(found) & {
        "licencas_sem_reconciliacao",
        "licencas_em_contingencia",
        "licencas_contingencia_esgotada",
        "licencas_com_falha",
    }


def test_health_flags_stale_reconciliation(env: Env, db: Database) -> None:
    cfg = settings_for(env, fmcommand_mode="shadow")
    env.sql("UPDATE license_sync_state SET last_success_at = %s", (NOW - timedelta(hours=3),))
    found = health.check(db, SimulatedSender(), now=NOW, require_worker=False, settings=cfg)
    assert "licencas_sem_reconciliacao" in codes(found)
    env.sql("UPDATE license_sync_state SET last_success_at = %s", (NOW,))
    found = health.check(db, SimulatedSender(), now=NOW, require_worker=False, settings=cfg)
    assert "licencas_sem_reconciliacao" not in codes(found)


def test_cli_status_and_reconcile_off(
    env: Env, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import argparse

    off = settings_for(env, fmcommand_mode="off", database_admin_url=env.admin_url)
    ns = argparse.Namespace(cmd="license-reconcile")
    assert cli.license_command(off, ns) == 0
    assert "Modo off" in capsys.readouterr().out
    assert cli.license_command(off, argparse.Namespace(cmd="license-status")) == 0
    assert "Modo: off" in capsys.readouterr().out


def test_cli_release_requires_approval_and_known_tenant(env: Env) -> None:
    import argparse
    import uuid

    cfg = settings_for(env, database_admin_url=env.admin_url)
    ns = argparse.Namespace(
        cmd="license-release", tenant=str(uuid.uuid4()), approved_by="x", evidence="y"
    )
    assert cli.license_command(cfg, ns) == 1


def test_shadow_webhook_leaves_real_licenses_untouched(env: Env, db: Database) -> None:
    lic = Lic()
    with license_client(env, db, fmcommand_mode="shadow") as c:
        r = post_event(c, lic.event())
        assert r.status_code in (200, 202)
    assert (
        env.sql("SELECT count(*) FROM commercial_links WHERE license_id = %s", (lic.license_id,))[
            0
        ][0]
        == 0
    )
