"""Evolution v2 webhook security and canonical IA ingress; no real provider calls."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fm_seller.channels import voice
from fm_seller.channels.evolution import EvolutionClient
from fm_seller.channels.evolution_runtime import (
    EvolutionSender,
    RoutedWhatsAppSender,
    webhook_token,
)
from fm_seller.errors import AppError
from fm_seller.recovery.senders import SendError, SimulatedSender
from tests.conftest import Env, login, unique_email
from tests.test_evolution_pairing import FakeEvolution
from tests.test_voice import FakeMeta, FakeTranscriber


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


def _audio_event(instance: str, mid: str = "voice1", *, wrapped: bool = False) -> bytes:
    message: dict[str, Any] = {"audioMessage": {"mimetype": "audio/ogg; codecs=opus", "ptt": True}}
    if wrapped:
        message = {"ephemeralMessage": {"message": message}}
    payload = json.loads(_event(instance, message_id=mid))
    payload["data"]["message"] = message
    payload["data"]["messageType"] = "audioMessage"
    return json.dumps(payload).encode()


def test_evolution_audio_visible_with_voice_disabled_and_no_hallucination(
    client: TestClient, env: Env
) -> None:
    tenant, instance, token = _provision(client, env)
    path = f"/v1/webhooks/evolution/{instance}"
    raw = _audio_event(instance)
    assert (
        client.post(path, content=raw, headers={"x-fm-evolution-auth": token}).json()["messages"]
        == 1
    )
    rows = env.sql(
        "SELECT body,handled,media_status,media_id FROM messages "
        "WHERE tenant_id=%s AND direction='in'",
        (tenant,),
    )
    assert rows == [("[áudio recebido; transcrição desativada]", True, "failed", None)]
    again = client.post(path, content=raw, headers={"x-fm-evolution-auth": token})
    assert again.json()["duplicates"] == 1


def test_pilot_unavailable_audio_requests_text_once_without_replying_to_other_contacts(
    client: TestClient, env: Env
) -> None:
    tenant, instance, token = _provision(client, env)
    cast(Any, client.app).state.settings = cast(Any, client.app).state.settings.model_copy(
        update={
            "voice_transcription": False,
            "pilot_greeting_enabled": True,
            "pilot_demo_enabled": True,
            "pilot_greeting_tenant_id": tenant,
            "pilot_greeting_phone": "5511999991234",
        }
    )
    url = f"/v1/webhooks/evolution/{instance}"
    raw = _audio_event(instance, "audio-pilot-unavailable")
    first = client.post(url, content=raw, headers={"x-fm-evolution-auth": token})
    second = client.post(url, content=raw, headers={"x-fm-evolution-auth": token})
    assert first.status_code == 200 and first.json()["messages"] == 1
    assert second.status_code == 200 and second.json()["duplicates"] == 1
    assert env.sql(
        "SELECT body,status,author FROM messages WHERE tenant_id=%s AND direction='out'",
        (tenant,),
    ) == [(voice.AUDIO_UNAVAILABLE_MESSAGE, "queued", "bot")]
    other = _audio_event(instance, "audio-other")
    other = other.replace(b"5511999991234@s.whatsapp.net", b"5511988887777@s.whatsapp.net")
    assert (
        client.post(url, content=other, headers={"x-fm-evolution-auth": token}).status_code == 200
    )
    assert env.sql(
        "SELECT count(*) FROM messages WHERE tenant_id=%s AND direction='out'", (tenant,)
    ) == [(1,)]


def test_pilot_failed_audio_after_retries_asks_for_text_without_handoff(
    client: TestClient, env: Env, db: Any
) -> None:
    tenant, instance, token = _provision(client, env)
    cast(Any, client.app).state.settings = cast(Any, client.app).state.settings.model_copy(
        update={
            "voice_transcription": True,
            "pilot_greeting_enabled": True,
            "pilot_demo_enabled": True,
            "pilot_greeting_tenant_id": tenant,
            "pilot_greeting_phone": "5511999991234",
        }
    )
    url = f"/v1/webhooks/evolution/{instance}"
    raw = _audio_event(instance, "audio-pilot-failed")
    assert client.post(url, content=raw, headers={"x-fm-evolution-auth": token}).status_code == 200

    class UnavailableAudio:
        def fetch_voice(
            self, name: str, mid: str, jid: str, *, max_bytes: int
        ) -> tuple[bytes, str]:
            raise AppError(503, "audio_unavailable", "Áudio não encontrado.")

    for expected in (1, 2, 3):
        stats = voice.transcribe_pending(
            db,
            env.box,
            FakeTranscriber(),
            FakeMeta().client(),
            max_bytes=2_000_000,
            evolution=cast(Any, UnavailableAudio()),
            pilot_tenant_id=uuid.UUID(tenant),
            pilot_phone="5511999991234",
        )
        assert stats.retry == (1 if expected < 3 else 0)
        assert stats.failed == (1 if expected == 3 else 0)
        env.sql("UPDATE messages SET claimed_at=NULL WHERE tenant_id=%s", (tenant,))
    assert env.sql(
        "SELECT body,handled,media_status,media_attempts FROM messages "
        "WHERE tenant_id=%s AND direction='in'",
        (tenant,),
    ) == [("[áudio não transcrito; equipe necessária]", True, "failed", 3)]
    assert env.sql(
        "SELECT body,author,status FROM messages WHERE tenant_id=%s AND direction='out'",
        (tenant,),
    ) == [(voice.AUDIO_UNAVAILABLE_MESSAGE, "bot", "queued")]
    assert env.sql(
        "SELECT status,handoff_reason FROM conversations WHERE tenant_id=%s", (tenant,)
    ) == [("bot", None)]


def test_evolution_audio_uses_existing_transcription_worker_and_opt_out(
    client: TestClient, env: Env, db: Any
) -> None:
    tenant, instance, token = _provision(client, env)
    cast(Any, client.app).state.settings = cast(Any, client.app).state.settings.model_copy(
        update={"voice_transcription": True}
    )
    path = f"/v1/webhooks/evolution/{instance}"
    raw = _audio_event(instance, wrapped=True)
    assert (
        client.post(path, content=raw, headers={"x-fm-evolution-auth": token}).json()["messages"]
        == 1
    )
    assert env.sql(
        "SELECT handled,media_id,media_status FROM messages WHERE tenant_id=%s",
        (tenant,),
    ) == [(True, "evo:voice1", "pending")]

    class FakeVoiceEvolution:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        def fetch_voice(
            self, name: str, mid: str, jid: str, *, max_bytes: int
        ) -> tuple[bytes, str]:
            self.calls.append((name, mid, jid))
            assert max_bytes == 2_000_000
            return b"OggS-audio-sintetico", "audio/ogg"

    evolution = FakeVoiceEvolution()
    processed = voice.transcribe_pending(
        db,
        env.box,
        FakeTranscriber("Sim, quero conhecer o plano"),
        FakeMeta().client(),
        max_bytes=2_000_000,
        evolution=cast(Any, evolution),
    )
    assert processed.transcribed >= 1
    assert evolution.calls == [(instance, "voice1", "5511999991234@s.whatsapp.net")]
    assert env.sql(
        "SELECT body,handled,media_status,media_id FROM messages WHERE tenant_id=%s",
        (tenant,),
    ) == [("Sim, quero conhecer o plano", False, "done", None)]


def test_evolution_audio_unauthenticated_or_group_is_not_stored(
    client: TestClient, env: Env
) -> None:
    tenant, instance, token = _provision(client, env)
    path = f"/v1/webhooks/evolution/{instance}"
    payload = _audio_event(instance)
    assert client.post(path, content=payload).status_code == 401
    group = payload.replace(b"@s.whatsapp.net", b"@g.us")
    assert (
        client.post(path, content=group, headers={"x-fm-evolution-auth": token}).json()["messages"]
        == 0
    )
    assert env.sql("SELECT count(*) FROM messages WHERE tenant_id=%s", (tenant,)) == [(0,)]


def test_evolution_voice_media_source_validation() -> None:
    import base64

    instance = "fmav-" + "a" * 32
    msg_id = "voice1"
    jid = "5511999991234@s.whatsapp.net"
    record = {
        "key": {"id": msg_id, "remoteJid": jid, "fromMe": False},
        "message": {"audioMessage": {"mimetype": "audio/ogg; codecs=opus"}},
    }

    class FakeMediaClient(EvolutionClient):
        def __init__(self) -> None:
            super().__init__(base_url="https://evolution.test", api_key="fake")
            self.calls: list[str] = []

        def _call(
            self,
            method: str,
            route: str,
            *,
            body: dict[str, Any] | None = None,
            max_response: int = 256_000,
        ) -> dict[str, Any]:
            self.calls.append(route)
            if "findMessages" in route:
                return {"messages": {"records": [record]}}
            return {"base64": base64.b64encode(b"OggS-audio-sintetico").decode()}

    client = FakeMediaClient()
    calls = client.calls
    assert client.fetch_voice(instance, msg_id, jid, max_bytes=2_000_000) == (
        b"OggS-audio-sintetico",
        "audio/ogg",
    )
    assert calls == [
        f"/chat/findMessages/{instance}",
        f"/chat/getBase64FromMediaMessage/{instance}",
    ]
    cast(dict[str, Any], record["key"])["remoteJid"] = "5511988881234@s.whatsapp.net"
    with pytest.raises(AppError):
        client.fetch_voice(instance, msg_id, jid, max_bytes=2_000_000)
    assert len(calls) == 4


@pytest.mark.parametrize(
    ("stored_jid", "alternate_jid", "from_me", "duplicates", "accepted"),
    [
        ("12345678901234@lid", None, False, False, True),
        ("12345678901234@lid", "5511999991234@s.whatsapp.net", False, False, True),
        ("12345678901234@lid", "5511988887777@s.whatsapp.net", False, False, False),
        ("12345678901234@lid", None, True, False, False),
        ("12345678901234@lid", None, False, True, False),
        ("5511988887777@s.whatsapp.net", None, False, False, False),
        ("garbage@lid", None, False, False, False),
    ],
)
def test_evolution_voice_lid_lookup_is_unique_inbound_and_phone_bound(
    stored_jid: str,
    alternate_jid: str | None,
    from_me: bool,
    duplicates: bool,
    accepted: bool,
) -> None:
    import base64

    instance = "fmav-" + "a" * 32
    msg_id = "real-whatsapp-audio-id"
    jid = "5511999991234@s.whatsapp.net"
    key: dict[str, Any] = {"id": msg_id, "remoteJid": stored_jid, "fromMe": from_me}
    if alternate_jid is not None:
        key["remoteJidAlt"] = alternate_jid
    record = {
        "key": key,
        "message": {"audioMessage": {"mimetype": "audio/ogg; codecs=opus"}},
    }

    class LidClient(EvolutionClient):
        def __init__(self) -> None:
            super().__init__(base_url="https://evolution.test", api_key="fake")
            self.searches: list[dict[str, Any]] = []
            self.downloads = 0

        def _call(
            self,
            method: str,
            route: str,
            *,
            body: dict[str, Any] | None = None,
            max_response: int = 256_000,
        ) -> dict[str, Any]:
            if "findMessages" in route:
                assert body is not None
                self.searches.append(body)
                # Strict canonical JID is absent from the Evolution LID index.
                if "remoteJid" in body["where"]["key"]:
                    return {"messages": []}
                return {"messages": [record, record] if duplicates else [record]}
            self.downloads += 1
            return {"base64": base64.b64encode(b"OggS-audio-sintetico").decode()}

    client = LidClient()
    if accepted:
        assert client.fetch_voice(instance, msg_id, jid, max_bytes=2_000_000) == (
            b"OggS-audio-sintetico",
            "audio/ogg",
        )
        assert client.downloads == 1
    else:
        with pytest.raises(AppError) as err:
            client.fetch_voice(instance, msg_id, jid, max_bytes=2_000_000)
        assert err.value.code == "audio_lookup_mismatch"
        assert client.downloads == 0
    assert client.searches == [
        {"where": {"key": {"id": msg_id, "remoteJid": jid}}, "page": 1, "offset": 10},
        {"where": {"key": {"id": msg_id}}, "page": 1, "offset": 10},
    ]


class FlakyStateClient(FakeSendClient):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures
        self.state_calls = 0

    def state(self, name: str) -> str:
        self.state_calls += 1
        if self.state_calls <= self.failures:
            raise AppError(502, "evolution_unavailable", "indisponível")
        return self.state_value


def test_state_check_retries_transient_failure_before_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("fm_seller.channels.evolution_runtime._STATE_RETRY_DELAYS", (0, 0, 0))
    config = {"instance_name": "fmav-" + "a" * 32, "__provider": "evolution"}
    flaky = FlakyStateClient(failures=2)
    assert EvolutionSender(cast(Any, flaky)).send_text(config, "5511999991234", "Oi") == "ABC123"
    assert flaky.state_calls == 3 and len(flaky.sent) == 1
    down = FlakyStateClient(failures=99)
    with pytest.raises(SendError, match="não verificada"):
        EvolutionSender(cast(Any, down)).send_text(config, "5511999991234", "Oi")
    assert down.state_calls == 4 and down.sent == []
