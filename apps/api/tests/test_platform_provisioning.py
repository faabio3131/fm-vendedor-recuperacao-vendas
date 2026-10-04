"""Compra do próprio SaaS vira conta. Payloads sintéticos (formato real a confirmar)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fm_seller.api.app import create_app
from fm_seller.cli import map_product
from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.provisioning.platform import reprocess_platform
from tests.conftest import ORIGIN, Env, login, unique_email

SECRET = "segredo-plataforma-abc"


@pytest.fixture
def pclient(env: Env, db: Database) -> Iterator[TestClient]:
    cfg = Settings(**{**env.settings().model_dump(), "platform_cakto_secret": SECRET})
    with TestClient(create_app(cfg, db=db, box=env.box), headers={"Origin": ORIGIN}) as c:
        yield c


def purchase(
    email: str, event: str = "purchase_approved", product: str = "prod-x"
) -> dict[str, Any]:
    return {
        "event": event,
        "secret": SECRET,
        "data": {
            "id": f"o-{email}-{event}-{product}",  # pedido novo = id novo
            "customer": {"name": "Comprador", "email": email, "phone": "11999998888"},
            "product": {"id": product, "name": "F&M Vendedor"},
            "amount": 197,
        },
    }


def plan_of(env: Env, email: str) -> tuple[str, str] | None:
    rows = env.sql(
        "SELECT tp.plan_key, tp.status FROM tenant_plans tp JOIN pending_invites i "
        "ON i.tenant_id = tp.tenant_id WHERE lower(i.email) = lower(%s)",
        (email,),
    )
    return None if not rows else (rows[0][0], rows[0][1])


def test_purchase_creates_tenant_plan_and_invite_and_buyer_can_login(
    pclient: TestClient, env: Env
) -> None:
    map_product(env.admin_url, "cakto", "prod-x", "fase-2")
    email = unique_email("buyer")
    res = pclient.post("/v1/platform/webhooks/cakto", json=purchase(email))
    assert res.status_code == 200 and res.json() == {"status": "tenant_created"}
    assert plan_of(env, email) == ("fase-2", "active")
    assert login(pclient, email).status_code == 200
    me = pclient.get("/v1/me").json()
    assert me["plan"] == {"key": "fase-2", "status": "active"}
    assert "billing.gateway" in me["features"]


def test_repeated_event_and_second_purchase_do_not_duplicate_tenant(
    pclient: TestClient, env: Env
) -> None:
    map_product(env.admin_url, "cakto", "prod-x", "fase-1")
    email = unique_email("dup")
    body = purchase(email)
    assert (
        pclient.post("/v1/platform/webhooks/cakto", json=body).json()["status"] == "tenant_created"
    )
    assert pclient.post("/v1/platform/webhooks/cakto", json=body).json()["status"] == "duplicate"
    again = purchase(email, "subscription_renewed")
    assert (
        pclient.post("/v1/platform/webhooks/cakto", json=again).json()["status"] == "plan_updated"
    )
    count = env.sql("SELECT count(*) FROM pending_invites WHERE lower(email) = lower(%s)", (email,))
    assert count[0][0] == 1


def test_upgrade_changes_plan(pclient: TestClient, env: Env) -> None:
    map_product(env.admin_url, "cakto", "prod-x", "fase-1")
    map_product(env.admin_url, "cakto", "prod-up", "fase-3")
    email = unique_email("up")
    pclient.post("/v1/platform/webhooks/cakto", json=purchase(email))
    pclient.post("/v1/platform/webhooks/cakto", json=purchase(email, product="prod-up"))
    assert plan_of(env, email) == ("fase-3", "active")


def test_late_cancel_and_recovery_change_status(pclient: TestClient, env: Env) -> None:
    map_product(env.admin_url, "cakto", "prod-x", "fase-1")
    email = unique_email("st")
    pclient.post("/v1/platform/webhooks/cakto", json=purchase(email))
    for event, expected in (
        ("subscription_late", "past_due"),
        ("subscription_late_recovered", "active"),
        ("refund", "canceled"),
    ):
        out = pclient.post("/v1/platform/webhooks/cakto", json=purchase(email, event))
        assert out.json()["status"] == "plan_status_changed"
        assert plan_of(env, email) == ("fase-1", expected)


def test_canceled_plan_releases_no_features(pclient: TestClient, env: Env) -> None:
    map_product(env.admin_url, "cakto", "prod-x", "fase-1")
    email = unique_email("cx")
    pclient.post("/v1/platform/webhooks/cakto", json=purchase(email))
    assert login(pclient, email).status_code == 200
    assert pclient.get("/v1/me").json()["features"] != []
    pclient.post("/v1/platform/webhooks/cakto", json=purchase(email, "refund"))
    me = pclient.get("/v1/me").json()
    assert me["plan"]["status"] == "canceled" and me["features"] == []
    assert all(not prov["enabled"] for prov in pclient.get("/v1/providers").json())


def test_unmapped_product_is_kept_and_applied_after_mapping(
    pclient: TestClient, env: Env, db: Database
) -> None:
    email = unique_email("um")
    out = pclient.post("/v1/platform/webhooks/cakto", json=purchase(email, product="novo-prod"))
    assert out.json() == {"status": "unmapped_product"}
    assert plan_of(env, email) is None
    map_product(env.admin_url, "cakto", "novo-prod", "fase-1")
    assert reprocess_platform(db, env.box) >= 1
    assert plan_of(env, email) == ("fase-1", "active")


def test_security(pclient: TestClient, env: Env) -> None:
    bad = purchase(unique_email("s"))
    bad["secret"] = "errado"
    assert pclient.post("/v1/platform/webhooks/cakto", json=bad).status_code == 401
    assert pclient.post("/v1/platform/webhooks/hotmart", json={}).status_code == 404  # desligado
    assert pclient.post("/v1/platform/webhooks/outro", json={}).status_code == 404


def test_disabled_when_secret_not_configured(client: TestClient) -> None:
    assert client.post("/v1/platform/webhooks/cakto", json={"secret": ""}).status_code == 404


def test_no_email_is_flagged_not_provisioned(pclient: TestClient, env: Env) -> None:
    map_product(env.admin_url, "cakto", "prod-x", "fase-1")
    body = purchase("x@example.test")
    body["data"]["customer"] = {"name": "Sem email"}
    assert pclient.post("/v1/platform/webhooks/cakto", json=body).json() == {"status": "no_email"}
