"""Provas internas da jornada QR sem acessar Contabo ou contas reais."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from fm_seller.channels.evolution import EvolutionClient, InstanceMissing
from fm_seller.errors import AppError
from tests.conftest import Env, login, unique_email

PNG = b"\x89PNG\r\n\x1a\n" + b"fixture-interna"
QR = "data:image/png;base64," + base64.b64encode(PNG).decode()


class FakeEvolution:
    def __init__(self) -> None:
        self.current = "missing"
        self.created: list[str] = []

    def state(self, name: str) -> str:
        if name not in self.created or self.current == "missing":
            raise InstanceMissing()
        if self.current == "failure":
            raise AppError(503, "evolution_unreachable", "Falha de rede.")
        return self.current

    def create(self, name: str) -> None:
        self.created.append(name)
        self.current = "connecting"

    def qr(self, name: str) -> str:
        return QR


def _owner(client: TestClient, env: Env) -> str:
    email = unique_email("evolution-owner")
    tenant = env.tenant("Evolution QR", email, plan="fase-1")
    assert login(client, email).status_code == 200
    return tenant


def _enable(client: TestClient, fake: FakeEvolution) -> None:
    from typing import Any, cast

    service = cast(Any, client.app).state.evolution_pairing
    service._settings = service._settings.model_copy(update={"evolution_enabled": True})
    service.client = fake


def test_off_by_default_and_generic_mutations_blocked(client: TestClient, env: Env) -> None:
    _owner(client, env)
    assert client.get("/v1/connections/evolution/status").json()["status"] == "unavailable"
    assert client.post("/v1/connections/evolution/connect").status_code == 503
    for method, path in (
        ("PUT", "/v1/connections/evolution"),
        ("POST", "/v1/connections/evolution/test"),
        ("DELETE", "/v1/connections/evolution"),
    ):
        assert client.request(method, path, json={"values": {}}).status_code == 409
    assert client.get("/v1/connections").json() == []


def test_qr_is_ephemeral_and_connection_needs_provider_proof(client: TestClient, env: Env) -> None:
    tenant = _owner(client, env)
    fake = FakeEvolution()
    _enable(client, fake)
    assert client.get("/v1/connections/evolution/status").json()["status"] == "not_configured"
    result = client.post("/v1/connections/evolution/connect")
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "pending"
    assert result.json()["qr_code"] == QR
    assert 0 < result.json()["expires_in_seconds"] <= 30
    assert len(fake.created) == 1
    assert client.get("/v1/connections/evolution/status").json()["connected"] is False
    rows = env.sql(
        "SELECT config_encrypted, config_hint::text, status "
        "FROM connections WHERE tenant_id = %s AND provider = 'evolution'",
        (tenant,),
    )
    assert len(rows) == 1 and rows[0][2] == "pending"
    assert QR not in str(rows) and "data:image/png" not in str(rows)
    listing = client.get("/v1/connections").text
    assert QR not in listing
    fake.current = "open"
    verified = client.get("/v1/connections/evolution/status").json()
    assert verified == {"status": "connected", "connected": True}
    assert client.post("/v1/connections/evolution/connect").json() == verified
    assert len(fake.created) == 1
    fake.current = "close"
    assert client.get("/v1/connections/evolution/status").json()["status"] == "disconnected"
    assert client.get("/v1/connections").json()[0]["status"] == "disconnected"


def test_tenant_and_role_isolation(client: TestClient, env: Env) -> None:
    first = _owner(client, env)
    fake = FakeEvolution()
    _enable(client, fake)
    assert client.post("/v1/connections/evolution/connect").status_code == 200
    email = unique_email("evolution-agent")
    env.invite(first, email, "agent")
    client.cookies.clear()
    assert login(client, email).status_code == 200
    assert client.get("/v1/connections/evolution/status").status_code == 403
    assert client.post("/v1/connections/evolution/connect").status_code == 403
    client.cookies.clear()
    second = _owner(client, env)
    assert second != first
    assert client.get("/v1/connections/evolution/status").json()["status"] == "not_configured"
    assert client.post("/v1/connections/evolution/connect").status_code == 200
    assert len(set(fake.created)) == 2
    assert client.get("/v1/connections").json()[0]["provider"] == "evolution"


def test_failure_cannot_keep_stale_connected(client: TestClient, env: Env) -> None:
    _owner(client, env)
    fake = FakeEvolution()
    _enable(client, fake)
    assert client.post("/v1/connections/evolution/connect").status_code == 200
    fake.current = "open"
    assert client.get("/v1/connections/evolution/status").json()["connected"] is True
    fake.current = "failure"
    failed = client.get("/v1/connections/evolution/status")
    assert failed.status_code == 503
    assert client.get("/v1/connections").json()[0]["status"] == "needs_attention"
    fake.current = "missing"
    assert client.get("/v1/connections/evolution/status").json()["status"] == "disconnected"


def test_evolution_http_contract_and_rejects_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[httpx.Request] = []
    reported_name: dict[str, str | None] = {"name": "fmav-" + "a" * 32}
    raw_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if "connectionState" in request.url.path:
            return httpx.Response(
                200, json={"instance": {"instanceName": reported_name["name"], "state": "open"}}
            )
        if "/connect/" in request.url.path:
            return httpx.Response(200, json={"base64": QR})
        return httpx.Response(200, json={"instance": {"instanceName": "fmav-" + "a" * 32}})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "fm_seller.channels.evolution.httpx.Client",
        lambda **kw: raw_client(transport=transport, **kw),
    )
    client = EvolutionClient(base_url="https://evolution.example.test", api_key="s" * 40)
    name = "fmav-" + "a" * 32
    assert client.state(name) == "open"
    reported_name["name"] = "fmav-" + "b" * 32
    with pytest.raises(AppError):
        client.state(name)
    reported_name["name"] = None
    with pytest.raises(AppError):
        client.state(name)
    assert client.qr(name) == QR
    client.create(name)
    assert all(req.headers["apikey"] == "s" * 40 for req in calls)
    assert json.loads(calls[-1].content)["integration"] == "WHATSAPP-BAILEYS"
    with pytest.raises(AppError):
        client.state("../../other-tenant")
