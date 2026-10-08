"""Reconciliação pela API de licenças do Command: correção, recuperação e falhas sem efeito."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest

from fm_seller.cli import map_product
from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.licensing import reconcile as rec
from tests.conftest import Env, unique_email
from tests.license_helpers import (
    API_TOKEN,
    NOW,
    Lic,
    license_client,
    post_event,
    settings_for,
    uuid7,
)

BASE = "https://command.example.test"


class FakeApi:
    """Command falso: devolve páginas prontas e guarda o que foi pedido."""

    def __init__(self, pages: list[list[Lic]] | None = None, *, status: int = 200) -> None:
        self.pages = pages or [[]]
        self.status = status
        self.calls: list[httpx.Request] = []
        self.override: httpx.Response | None = None
        self.extra_items: list[Any] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        assert request.method == "GET"  # a API do Command só é lida, nunca escrita
        if self.override is not None:
            return self.override
        if self.status != 200:
            return httpx.Response(self.status, json={})
        assert request.headers["authorization"] == f"Bearer {API_TOKEN}"
        assert request.url.path == "/v1/licenses"
        cursor = request.url.params.get("cursor")
        index = int(cursor) if cursor else 0
        items = [lic.item() for lic in self.pages[index]] + (self.extra_items if index == 0 else [])
        nxt = str(index + 1) if index + 1 < len(self.pages) else None
        return httpx.Response(
            200,
            json={
                "schema_version": "fmcc.license.v1",
                "items": items,
                "next_cursor": nxt,
                "as_of": "2026-10-08T12:00:00Z",
            },
        )

    def client(self) -> rec.LicenseApiClient:
        return rec.LicenseApiClient(
            BASE,
            API_TOKEN,
            "ATENDEVENDEIA",
            transport=httpx.MockTransport(self.handler),
        )


@pytest.fixture(autouse=True)
def plans(env: Env) -> None:
    map_product(env.admin_url, "fmcommand", "plano-teste", "fase-2")


def run(
    env: Env, db: Database, api: FakeApi, *, cfg: Settings | None = None, now: Any = NOW
) -> Any:
    cfg = cfg or settings_for(env)
    return rec.reconcile(db, env.box, cfg, api.client(), now=now)


def link(env: Env, lic: Lic) -> tuple[Any, ...] | None:
    rows = env.sql(
        "SELECT tenant_id, authority, license_version, state FROM commercial_links "
        "WHERE license_id = %s",
        (lic.license_id,),
    )
    return rows[0] if rows else None


def plan_status(env: Env, tenant_id: Any) -> str:
    return str(env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (tenant_id,))[0][0])


def sync(env: Env) -> tuple[Any, ...]:
    return env.sql(
        "SELECT last_attempt_at, last_success_at, last_error, consecutive_failures "
        "FROM license_sync_state"
    )[0]


def test_lost_webhook_provisioning_is_recovered_by_the_api(env: Env, db: Database) -> None:
    lic = Lic()
    result = run(env, db, FakeApi([[lic]]))
    assert result.ok and result.provisioned == 1
    row = link(env, lic)
    assert row is not None and row[1] == "fmcommand" and row[3] == "active"
    invites = env.sql("SELECT count(*) FROM pending_invites WHERE tenant_id = %s", (row[0],))
    assert invites == [(1,)]
    assert sync(env)[2] is None and sync(env)[3] == 0  # sem erro


def test_reconciling_twice_never_duplicates_accounts(env: Env, db: Database) -> None:
    lic = Lic()
    api = FakeApi([[lic]])
    run(env, db, api)
    again = run(env, db, api)
    assert again.ok and again.provisioned == 0
    assert env.sql(
        "SELECT count(*) FROM commercial_links WHERE license_id = %s", (lic.license_id,)
    ) == [(1,)]
    assert env.sql(
        "SELECT count(*) FROM pending_invites WHERE lower(email) = lower(%s)", (lic.admin_email,)
    ) == [(1,)]


def test_lost_suspension_event_is_applied_by_reconciliation(env: Env, db: Database) -> None:
    lic = Lic()
    with license_client(env, db) as c:
        assert post_event(c, lic.event()).json() == {"status": "provisioned"}
    newer = lic.at(version=3, state="suspended")
    result = run(env, db, FakeApi([[newer]]))
    assert result.ok and result.applied == 1
    row = link(env, lic)
    assert row is not None and row[2] == 3 and plan_status(env, row[0]) == "suspended"


def test_same_version_only_renews_validation(env: Env, db: Database) -> None:
    lic = Lic()
    run(env, db, FakeApi([[lic]]), now=NOW)
    before = env.sql(
        "SELECT last_validated_at FROM commercial_links WHERE license_id = %s", (lic.license_id,)
    )[0][0]
    later = NOW + timedelta(minutes=15)
    run(env, db, FakeApi([[lic]]), now=later)
    after = env.sql(
        "SELECT last_validated_at FROM commercial_links WHERE license_id = %s", (lic.license_id,)
    )[0][0]
    assert after - before == timedelta(minutes=15)


def test_pagination_reads_every_page(env: Env, db: Database) -> None:
    a, b, c = Lic(), Lic(), Lic()
    api = FakeApi([[a, b], [c]])
    result = run(env, db, api)
    assert result.licenses == 3 and result.provisioned == 3
    assert [r.url.params.get("cursor") for r in api.calls] == [None, "1"]
    assert all(link(env, x) is not None for x in (a, b, c))


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (500, "command_indisponivel"),
        (503, "command_indisponivel"),
        (429, "command_indisponivel"),
        (401, "credencial_recusada"),
        (403, "credencial_recusada"),
        (404, "resposta_404"),
    ],
)
def test_api_failures_change_nothing_and_record_only_a_code(
    env: Env, db: Database, status: int, error: str
) -> None:
    lic = Lic()
    with license_client(env, db) as c:
        assert post_event(c, lic.event()).json() == {"status": "provisioned"}
    failures_before = sync(env)[3]
    result = run(env, db, FakeApi(status=status))
    assert not result.ok and result.error == error
    row = link(env, lic)
    assert row is not None and row[2] == 1 and row[3] == "active"
    assert sync(env)[2] == error and sync(env)[3] == failures_before + 1
    assert API_TOKEN not in str(sync(env))


def test_garbage_response_is_rejected_without_effect(env: Env, db: Database) -> None:
    api = FakeApi()
    api.override = httpx.Response(200, text="isto nao e json")
    assert run(env, db, api).error == "resposta_invalida"
    api.override = httpx.Response(200, json={"schema_version": "v9", "items": []})
    assert run(env, db, api).error == "resposta_invalida"
    api.override = httpx.Response(200, json={"schema_version": "fmcc.license.v1", "items": "x"})
    assert run(env, db, api).error == "resposta_invalida"


def test_one_invalid_item_rejects_the_whole_response_atomically(env: Env, db: Database) -> None:
    good = Lic()
    api = FakeApi([[good]])
    bad = Lic().item()
    bad["customer_id"] = "cliente@example.test"  # e-mail como chave: fora do contrato
    api.extra_items = [bad]
    result = run(env, db, api)
    assert not result.ok and result.error == "item_fora_do_contrato"
    assert link(env, good) is None  # nem o item bom foi aplicado


def test_other_product_in_the_listing_is_refused(env: Env, db: Database) -> None:
    other = Lic(product="KORDENA")
    result = run(env, db, FakeApi([[other]]))
    assert not result.ok and result.error == "produto_diferente"
    assert link(env, other) is None


def test_redirects_are_never_followed_with_the_token(env: Env, db: Database) -> None:
    api = FakeApi()
    api.override = httpx.Response(302, headers={"Location": "https://mal.example.test/x"})
    result = run(env, db, api)
    assert not result.ok and result.error == "resposta_302"
    assert len(api.calls) == 1


def test_client_requires_https_and_a_strong_token() -> None:
    for base in ("http://command.example.test", "https://u:p@command.example.test", "ftp://x", ""):
        with pytest.raises(rec.ReconcileError):
            rec.LicenseApiClient(base, API_TOKEN, "ATENDEVENDEIA")
    with pytest.raises(rec.ReconcileError):
        rec.LicenseApiClient(BASE, "curto", "ATENDEVENDEIA")


def test_license_missing_in_the_command_is_audited_once_and_not_suspended(
    env: Env, db: Database
) -> None:
    lic = Lic()
    run(env, db, FakeApi([[lic]]))
    row = link(env, lic)
    assert row is not None
    run(env, db, FakeApi([[]]), now=NOW + timedelta(minutes=15))
    run(env, db, FakeApi([[]]), now=NOW + timedelta(minutes=30))
    audits = env.sql(
        "SELECT count(*) FROM audit_log WHERE tenant_id = %s "
        "AND action = 'license.missing_in_command'",
        (row[0],),
    )
    assert audits == [(1,)]
    assert plan_status(env, row[0]) == "active"


def test_unmapped_plan_is_recovered_after_the_mapping_exists(env: Env, db: Database) -> None:
    lic = Lic(plan_code="plano-so-depois")
    assert run(env, db, FakeApi([[lic]])).provisioned == 0
    assert link(env, lic) is None
    map_product(env.admin_url, "fmcommand", "plano-so-depois", "fase-3")
    assert run(env, db, FakeApi([[lic]])).provisioned == 1
    row = link(env, lic)
    assert row is not None
    assert env.sql("SELECT plan_key FROM tenant_plans WHERE tenant_id = %s", (row[0],)) == [
        ("fase-3",)
    ]


def test_unauthorized_license_is_not_provisioned_by_reconciliation(env: Env, db: Database) -> None:
    lic = Lic(authorized=False)
    assert run(env, db, FakeApi([[lic]])).provisioned == 0
    assert link(env, lic) is None


def test_existing_direct_account_is_reported_not_duplicated(env: Env, db: Database) -> None:
    email = unique_email("direto")
    tenant_id = env.tenant("Loja Direta", email, plan="fase-1")
    lic = Lic(admin_email=email)
    result = run(env, db, FakeApi([[lic]]))
    assert result.ok and result.pending_adoption == 1 and result.provisioned == 0
    assert link(env, lic) is None
    assert env.sql("SELECT count(*) FROM pending_invites WHERE tenant_id = %s", (tenant_id,)) == [
        (1,)
    ]


def test_shadow_reconciliation_records_and_never_applies(env: Env, db: Database) -> None:
    lic = Lic()
    cfg = settings_for(env, fmcommand_mode="shadow")
    result = run(env, db, FakeApi([[lic]]), cfg=cfg)
    assert result.ok and result.provisioned == 0
    assert link(env, lic) is None
    assert env.sql(
        "SELECT count(*) FROM license_shadow WHERE license_id = %s", (lic.license_id,)
    ) == [(1,)]


# ------------------------------------------------------------------ ciclo do worker


def test_cycle_is_a_noop_when_off_and_never_touches_the_network(env: Env, db: Database) -> None:
    called: list[int] = []

    def factory(_s: Settings) -> rec.LicenseApiClient | None:
        called.append(1)
        return None

    out = rec.cycle(
        db, env.box, settings_for(env, fmcommand_mode="off"), now=NOW, client_factory=factory
    )
    assert out == {} and called == []
    assert rec.build_client(settings_for(env, fmcommand_mode="off")) is None


def test_cycle_reconciles_only_when_due(env: Env, db: Database) -> None:
    api = FakeApi([[]])
    cfg = settings_for(env)
    env.sql("UPDATE license_sync_state SET last_attempt_at = NULL")
    start = NOW + timedelta(days=400)

    def factory(_s: Settings) -> rec.LicenseApiClient:
        return api.client()

    assert "reconciliacao" in rec.cycle(db, env.box, cfg, now=start, client_factory=factory)
    n = len(api.calls)
    assert "reconciliacao" not in rec.cycle(
        db, env.box, cfg, now=start + timedelta(minutes=14), client_factory=factory
    )
    assert len(api.calls) == n
    assert "reconciliacao" in rec.cycle(
        db, env.box, cfg, now=start + timedelta(minutes=16), client_factory=factory
    )
    assert len(api.calls) == n + 1


def test_cycle_without_api_configured_skips_the_network(env: Env, db: Database) -> None:
    cfg = settings_for(
        env, fmcommand_mode="shadow", fmcommand_api_base_url="", fmcommand_api_token=""
    )
    out = rec.cycle(db, env.box, cfg, now=NOW)
    assert "reconciliacao" not in out and out["contingencia"] == {"iniciadas": 0, "encerradas": 0}
    with pytest.raises(ValueError):  # enforce sem a API é recusado já na configuração
        settings_for(env, fmcommand_api_base_url="", fmcommand_api_token="")


def test_each_license_failure_does_not_stop_the_others(
    env: Env, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fm_seller.licensing import service

    first, second = Lic(), Lic()
    real = service._dispatch

    def flaky(settings: Settings, conn: Any, lic: Any, *a: Any, **k: Any) -> Any:
        if lic.license_id == uuid_of(first):
            raise RuntimeError("falha isolada")
        return real(settings, conn, lic, *a, **k)

    def uuid_of(x: Lic) -> Any:
        import uuid

        return uuid.UUID(x.license_id)

    monkeypatch.setattr(service, "_dispatch", flaky)
    result = run(env, db, FakeApi([[first, second]]))
    assert result.ok and result.failed == 1 and result.provisioned == 1
    assert link(env, first) is None and link(env, second) is not None
    assert uuid7()  # gerador de teste segue válido
