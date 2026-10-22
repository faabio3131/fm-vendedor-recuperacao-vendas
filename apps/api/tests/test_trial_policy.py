"""Trial 7d / 500 automatic sends: deterministic persistence and fail-closed boundaries."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from fm_seller import platform_admin as pa
from fm_seller.db import Database
from fm_seller.errors import AppError
from fm_seller.provisioning import trial
from tests.conftest import Env, login, unique_email

START = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def _enroll(client: TestClient, env: Env, db: Database) -> uuid.UUID:
    email = unique_email("trial")
    tenant = uuid.UUID(env.tenant("Piloto trial", email))
    assert login(client, email).status_code == 200
    actor = env.sql("SELECT user_id FROM memberships WHERE tenant_id = %s", (tenant,))[0][0]
    state = trial.start(db, tenant, actor=actor, now=START)
    assert state["limit"] == 500 and state["warn_at"] == 400
    assert state["status"] == "active"
    assert state["expires_at"] == START + timedelta(days=7)
    return tenant


def test_trial_enrollment_preserves_legacy_and_cannot_restart(
    client: TestClient, env: Env, db: Database
) -> None:
    email = unique_email("trial-baseline")
    tenant = uuid.UUID(env.tenant("Plano histórico", email))
    assert trial.reserve(db, tenant, source_kind="bot", source_id=uuid.uuid4()) == "paid"
    assert login(client, email).status_code == 200
    actor = env.sql("SELECT user_id FROM memberships WHERE tenant_id = %s", (tenant,))[0][0]
    trial.start(db, tenant, actor=actor, now=START)
    with pytest.raises(AppError) as rejected:
        trial.start(db, tenant, actor=actor, now=START)
    assert rejected.value.code == "trial_used"

    # Converting to an ordinary paid plan retains trial history and does not reset it.
    env.sql("UPDATE tenant_plans SET plan_key = 'av-essencial' WHERE tenant_id = %s", (tenant,))
    assert trial.reserve(db, tenant, source_kind="bot", source_id=uuid.uuid4()) == "paid"
    with pytest.raises(AppError):
        trial.start(db, tenant, actor=actor, now=START)


def test_shared_cap_warning_expiry_and_idempotency(
    client: TestClient, env: Env, db: Database
) -> None:
    tenant = _enroll(client, env, db)
    first = uuid.uuid4()
    assert trial.reserve(db, tenant, source_kind="bot", source_id=first, now=START) == "reserved"
    assert trial.reserve(db, tenant, source_kind="bot", source_id=first, now=START) == "duplicate"
    assert trial.finalize(db, tenant, source_kind="bot", source_id=first, outcome="sent")
    assert not trial.finalize(db, tenant, source_kind="bot", source_id=first, outcome="sent")

    # Start at 399 to exercise the exact warning/limit boundary without 399 real sends.
    env.sql(
        "UPDATE tenant_plans SET trial_messages_sent = 399 WHERE tenant_id = %s",
        (tenant,),
    )
    with db.tx(tenant_id=tenant) as conn:
        state = trial.snapshot(conn, tenant, now=START + timedelta(days=1))
        assert state is not None and state["used"] == 399
        assert state["conversion_prompt"] is False

    one = uuid.uuid4()
    assert (
        trial.reserve(
            db, tenant, source_kind="recovery", source_id=one, now=START + timedelta(days=1)
        )
        == "reserved"
    )
    assert trial.finalize(db, tenant, source_kind="recovery", source_id=one, outcome="sent")
    with db.tx(tenant_id=tenant) as conn:
        state = trial.snapshot(conn, tenant, now=START + timedelta(days=1))
        assert state is not None and state["used"] == 400
        assert state["conversion_prompt"] is True

    env.sql("UPDATE tenant_plans SET trial_messages_sent = 499 WHERE tenant_id = %s", (tenant,))
    last = uuid.uuid4()
    assert trial.reserve(db, tenant, source_kind="bot", source_id=last, now=START) == "reserved"
    assert (
        trial.reserve(db, tenant, source_kind="recovery", source_id=uuid.uuid4(), now=START)
        == "blocked"
    )
    assert trial.finalize(db, tenant, source_kind="bot", source_id=last, outcome="sent")
    with db.tx(tenant_id=tenant) as conn:
        state = trial.snapshot(conn, tenant, now=START)
        assert state is not None and state["status"] == "exhausted"
        assert state["used"] == 500
        assert trial.blocked(conn, tenant, now=START)

    expiring = _enroll(client, env, db)
    with db.tx(tenant_id=expiring) as conn:
        state = trial.snapshot(conn, expiring, now=START + timedelta(days=7))
        assert state is not None and state["status"] == "expired"
    assert (
        trial.reserve(
            db,
            expiring,
            source_kind="bot",
            source_id=uuid.uuid4(),
            now=START + timedelta(days=7),
        )
        == "blocked"
    )


def test_failed_send_releases_slot_uncertain_conservatively_uses_it(
    client: TestClient, env: Env, db: Database
) -> None:
    tenant = _enroll(client, env, db)
    failed = uuid.uuid4()
    unknown = uuid.uuid4()
    assert trial.reserve(db, tenant, source_kind="bot", source_id=failed, now=START) == "reserved"
    assert trial.finalize(db, tenant, source_kind="bot", source_id=failed, outcome="failed")
    assert (
        trial.reserve(db, tenant, source_kind="recovery", source_id=unknown, now=START)
        == "reserved"
    )
    assert trial.finalize(
        db, tenant, source_kind="recovery", source_id=unknown, outcome="uncertain"
    )
    with db.tx(tenant_id=tenant) as conn:
        state = trial.snapshot(conn, tenant, now=START)
        assert state is not None and state["used"] == 0
        assert state["reserved"] == 1

    # Reconcile the uncertain send only after evidence of a definitive provider outcome.
    assert trial.finalize(
        db, tenant, source_kind="recovery", source_id=unknown, outcome="sent"
    )
    with db.tx(tenant_id=tenant) as conn:
        state = trial.snapshot(conn, tenant, now=START)
        assert state is not None and state["used"] == 1
        assert state["reserved"] == 0


def test_two_workers_cannot_exceed_last_shared_slot(
    client: TestClient, env: Env, db: Database
) -> None:
    tenant = _enroll(client, env, db)
    env.sql("UPDATE tenant_plans SET trial_messages_sent = 499 WHERE tenant_id = %s", (tenant,))
    bot, recovery = uuid.uuid4(), uuid.uuid4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(trial.reserve, db, tenant, source_kind="bot", source_id=bot, now=START)
        b = pool.submit(
            trial.reserve, db, tenant, source_kind="recovery", source_id=recovery, now=START
        )
        results = [a.result(), b.result()]
    assert sorted(results) == ["blocked", "reserved"]
    with db.tx(tenant_id=tenant) as conn:
        state = trial.snapshot(conn, tenant, now=START)
        assert state is not None and state["reserved"] == 1
        assert state["remaining"] == 0


def test_trial_admin_grant_requires_platform_role_and_generic_plan_change_is_denied(
    client: TestClient, env: Env
) -> None:
    owner = unique_email("trial-owner")
    tenant = env.tenant("Cliente do teste interno", owner)
    assert login(client, owner).status_code == 200
    path = f"/v1/admin/clients/{tenant}/trial"
    assert client.post(path).status_code == 403
    assert client.put(
        f"/v1/admin/clients/{tenant}/plan", json={"plan": "av-trial"}
    ).status_code == 403
    assert client.post("/v1/auth/logout").status_code == 200

    email = unique_email("trial-admin")
    assert pa.invite_admin(env.admin_url, email) == "convite"
    assert login(client, email).status_code == 200
    rejected = client.put(
        f"/v1/admin/clients/{tenant}/plan", json={"plan": "av-trial"}
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "trial_requires_grant"

    result = client.post(path)
    assert result.status_code == 200, result.text
    assert result.json()["limit"] == 500 and result.json()["warn_at"] == 400
    assert client.post(path).status_code == 400
    rows = env.sql(
        "SELECT plan_key, trial_message_cap, trial_warn_at, trial_started_at "
        "FROM tenant_plans WHERE tenant_id = %s",
        (tenant,),
    )
    assert len(rows) == 1
    assert rows[0][0:3] == ("av-trial", 500, 400)
    assert rows[0][3] is not None
