"""Evolution v2 webhook security and canonical IA ingress; no real provider calls."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fm_seller.channels.evolution_runtime import (
    EvolutionSender,
    RoutedWhatsAppSender,
    webhook_token,
)
from fm_seller.errors import AppError
from fm_seller.recovery.senders import SendError, SimulatedSender
from tests.conftest import Env, login, unique_email
from tests.test_evolution_pairing import FakeEvolution


def _provision(client: TestClient, env: Env) -> tuple[str, str, str]:
    email = unique_email("evolution-runtime")
    tenant = env.tenant("Webhook Evolution", email, plan="fase-1")
    assert login(client, email).status_code == 200
    pairing = cast(Any, client.app).state.evolution_pairing
    pairing._settings = pairing._settings.model_copy(
        update={"evolution_enabled": True, "evolution_webhook_key": SecretStr("w" * 48)}
    )
    fake = FakeEvolution()
    pairing.client = fake
    res = client.post("/v1/connections/evolution/connect")
    assert res.status_code == 200, res.text
    fake.current = "open"
    assert client.get("/v1/connections/evolution/status").json()["connected"] is True
    instance = "fmav-" + hashlib.sha256(uuid.UUID(tenant).bytes).hexdigest()[:32]
    key = webhook_token(pairing._settings, uuid.UUID(tenant), instance)
    # The webhook route reads app.state.settings, not an editable client-supplied flag.
    cast(Any, client.app).state.settings = pairing._settings
    return tenant, instance, key


def _event(instance: str, *, message_id: str = "m1", from_me: bool = False) -> bytes:
    return json.dumps(
        {
            "event": "messages.upsert",
            "instance": instance,
            "data": {
                "key": {
                    "remoteJid": "5511999991234@s.whatsapp.net",
                    "fromMe": from_me,
                    "id": message_id,
                },
                "message": {"conversation": "Oi, gostaria de saber o preço"},
                "pushName": "Cliente Piloto",
            },
        }
    ).encode()


def test_evolution_webhook_auth_replay_and_tenant_scope(client: TestClient, env: Env) -> None:
    tenant, instance, key = _provision(client, env)
    url = f"/v1/webhooks/evolution/{instance}"
    raw = _event(instance)
    assert client.post(url, content=raw).status_code == 401
    invalid = client.post(url, content=raw, headers={"x-fm-evolution-auth": "wrong"})
    assert invalid.status_code == 401
    assert (
        client.post(
            url, content=_event("fmav-" + "0" * 32), headers={"x-fm-evolution-auth": key}
        ).status_code
        == 403
    )
    ok = client.post(url, content=raw, headers={"x-fm-evolution-auth": key})
    assert ok.status_code == 200, ok.text
    assert ok.json()["messages"] == 1
    again = client.post(url, content=raw, headers={"x-fm-evolution-auth": key})
    assert again.status_code == 200 and again.json()["duplicates"] == 1
    events = env.sql(
        "SELECT provider_message_id, handled FROM messages "
        "WHERE tenant_id = %s AND direction = 'in'",
        (tenant,),
    )
    assert len(events) == 1 and events[0][0] == "evo:m1"
    ignored = client.post(
        url,
        content=_event(instance, message_id="m2", from_me=True),
        headers={"x-fm-evolution-auth": key},
    )
    assert ignored.status_code == 200 and ignored.json()["messages"] == 0
    group = (
        _event(instance, message_id="group").decode().replace("@s.whatsapp.net", "@g.us").encode()
    )
    assert (
        client.post(url, content=group, headers={"x-fm-evolution-auth": key}).json()["messages"]
        == 0
    )
    assert (
        len(env.sql("SELECT id FROM messages WHERE tenant_id = %s AND direction = 'in'", (tenant,)))
        == 1
    )


def test_webhook_refuses_unverified_connection(client: TestClient, env: Env) -> None:
    tenant, instance, key = _provision(client, env)
    env.sql(
        "UPDATE connections SET status = 'disconnected' "
        "WHERE tenant_id = %s AND provider = 'evolution'",
        (tenant,),
    )
    result = client.post(
        f"/v1/webhooks/evolution/{instance}",
        content=_event(instance),
        headers={"x-fm-evolution-auth": key},
    )
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "evolution_not_verified"


class FakeSendClient:
    def __init__(self) -> None:
        self.state_value = "open"
        self.sent: list[dict[str, Any]] = []
        self.result: dict[str, Any] = {"key": {"id": "ABC123"}}

    def state(self, name: str) -> str:
        return self.state_value

    def _call(self, method: str, route: str, *, body: dict[str, Any]) -> dict[str, Any]:
        assert method == "POST"
        assert route.startswith("/message/sendText/fmav-")
        self.sent.append(body)
        return self.result


def test_evolution_sender_no_fallback_or_uncertain_retry() -> None:
    client = FakeSendClient()
    sender = EvolutionSender(cast(Any, client))
    config = {"instance_name": "fmav-" + "a" * 32, "__provider": "evolution"}
    assert sender.send_text(config, "5511999991234", "Oi") == "ABC123"
    assert client.sent == [{"number": "5511999991234", "text": "Oi"}]
    router = RoutedWhatsAppSender(
        SimulatedSender(), sender, meta_enabled=False, evolution_enabled=True
    )
    assert router.send_text(config, "5511999991234", "Segundo") == "ABC123"
    with pytest.raises(SendError):
        router.send_text({"__provider": "whatsapp_cloud"}, "5511999991234", "Não enviar")
    client.state_value = "close"
    with pytest.raises(SendError):
        sender.send_text(config, "5511999991234", "Sem sessão")
    assert len(client.sent) == 2
    client.state_value = "open"
    client.result = {}
    with pytest.raises(RuntimeError):
        sender.send_text(config, "5511999991234", "incerto")
    assert len(client.sent) == 3


def test_webhook_token_isolation_and_fail_closed(client: TestClient) -> None:
    cfg = cast(Any, client.app).state.settings.model_copy(
        update={"evolution_enabled": True, "evolution_webhook_key": SecretStr("k" * 40)}
    )
    instance = "fmav-" + "a" * 32
    assert webhook_token(cfg, uuid.uuid4(), instance) != webhook_token(cfg, uuid.uuid4(), instance)
    with pytest.raises(AppError):
        webhook_token(
            cfg.model_copy(update={"evolution_webhook_key": SecretStr("")}), uuid.uuid4(), instance
        )
