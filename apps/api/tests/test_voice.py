"""Áudio do cliente (WhatsApp): transcrição vira texto comum; desligado por padrão."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from fm_seller import cli
from fm_seller.ai.gemini import AiModelError, GeminiModel
from fm_seller.api.app import create_app
from fm_seller.channels import voice
from fm_seller.channels.meta_api import MetaClient, MetaRejected, MetaUncertain
from fm_seller.config import Settings
from fm_seller.db import Database
from tests.conftest import ORIGIN, Env, login, unique_email
from tests.test_whatsapp_inbound import NUMBER, PHONE, payload, post, text

TOKEN = "EAAGtoken-do-cliente-voz"
AUDIO = b"OggS-audio-sintetico"


def audio(mid: str, media: str = "media-1") -> dict[str, Any]:
    return {"from": PHONE, "id": mid, "type": "audio", "audio": {"id": media, "voice": True}}


def app_for(env: Env, db: Database, *, voice_on: bool) -> Iterator[TestClient]:
    cfg = Settings(**{**env.settings().model_dump(), "voice_transcription": voice_on})
    with TestClient(create_app(cfg, db=db, box=env.box), headers={"Origin": ORIGIN}) as c:
        yield c


@pytest.fixture
def wa_on(env: Env, db: Database) -> Iterator[tuple[TestClient, str, str]]:
    yield from _wa(env, db, True)


@pytest.fixture
def wa_off(env: Env, db: Database) -> Iterator[tuple[TestClient, str, str]]:
    yield from _wa(env, db, False)


def _wa(env: Env, db: Database, on: bool) -> Iterator[tuple[TestClient, str, str]]:
    for c in app_for(env, db, voice_on=on):
        email = unique_email("voz")
        tid = env.tenant("Loja Voz", email)
        assert login(c, email).status_code == 200
        res = c.put(
            "/v1/connections/whatsapp_cloud",
            json={
                "values": {
                    "phone_number_id": NUMBER,
                    "waba_id": "2",
                    "access_token": TOKEN,
                    "app_secret": "app-secret-de-teste",
                }
            },
        )
        assert res.status_code == 200, res.text
        yield c, tid, res.json()["webhook_url"].rsplit("/", 1)[1]


class FakeMeta:
    """Graph API falsa: resolve a mídia e entrega os bytes. Guarda os tokens usados."""

    def __init__(self, *, mime: str = "audio/ogg; codecs=opus", size: int | None = None) -> None:
        self.mime, self.size = mime, size
        self.tokens: list[str] = []
        self.fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.tokens.append(request.headers.get("authorization", ""))
        if self.fail:
            return httpx.Response(500)
        if request.url.host == "graph.test":
            info: dict[str, Any] = {"url": "https://cdn.test/audio", "mime_type": self.mime}
            if self.size is not None:
                info["file_size"] = self.size
            return httpx.Response(200, json=info)
        return httpx.Response(200, content=AUDIO)

    def client(self) -> MetaClient:
        return MetaClient(base="https://graph.test", transport=httpx.MockTransport(self.handler))


class FakeTranscriber:
    def __init__(self, heard: str = "quero comprar o curso") -> None:
        self.heard, self.calls = heard, 0

    def transcribe(self, audio_bytes: bytes, mime_type: str) -> tuple[str, int, int]:
        self.calls += 1
        assert audio_bytes == AUDIO and mime_type == "audio/ogg"
        return self.heard, 10, 5


def msg_row(env: Env, tid: str) -> tuple[Any, ...]:
    return env.sql(
        "SELECT body, handled, media_status, media_id, media_attempts FROM messages "
        "WHERE tenant_id = %s AND direction = 'in'",
        (tid,),
    )[0]


def run(
    env: Env, db: Database, meta: FakeMeta, tr: Any, *, max_bytes: int = 1_000_000
) -> voice.VoiceStats:
    return voice.transcribe_pending(db, env.box, tr, meta.client(), max_bytes=max_bytes)


def release_claim(env: Env, tid: str) -> None:
    env.sql("UPDATE messages SET claimed_at = NULL WHERE tenant_id = %s", (tid,))


# ------------------------------------------------------------------ entrada


def test_voice_off_keeps_the_old_behavior(wa_off: tuple[TestClient, str, str], env: Env) -> None:
    c, tid, pid = wa_off
    assert post(c, pid, payload([audio("wamid.a1")])).status_code == 200
    assert msg_row(env, tid) == ("[mensagem do tipo audio]", False, None, None, 0)


def test_voice_on_holds_the_message_until_transcribed(
    wa_on: tuple[TestClient, str, str], env: Env
) -> None:
    c, tid, pid = wa_on
    assert post(c, pid, payload([audio("wamid.a2")])).status_code == 200
    assert msg_row(env, tid) == ("[áudio em transcrição]", True, "pending", "media-1", 0)


def test_text_messages_are_not_affected_by_voice(
    wa_on: tuple[TestClient, str, str], env: Env
) -> None:
    c, tid, pid = wa_on
    post(c, pid, payload([text("wamid.t1", "Oi")]))
    assert msg_row(env, tid) == ("Oi", False, None, None, 0)


# ------------------------------------------------------------------ transcrição


def test_transcription_replaces_the_text_and_releases_the_seller(
    wa_on: tuple[TestClient, str, str], env: Env, db: Database
) -> None:
    c, tid, pid = wa_on
    post(c, pid, payload([audio("wamid.b1")]))
    meta, tr = FakeMeta(), FakeTranscriber()
    stats = run(env, db, meta, tr)
    assert stats.transcribed >= 1  # a fila é global: pode haver sobras de outros testes
    assert msg_row(env, tid) == ("quero comprar o curso", False, "done", None, 1)
    assert set(meta.tokens) == {f"Bearer {TOKEN}"}  # token do cliente dono da mensagem
    run(env, db, meta, tr)
    assert msg_row(env, tid)[4] == 1  # já pronta: não é reprocessada


def test_spoken_stop_request_is_honored(
    wa_on: tuple[TestClient, str, str], env: Env, db: Database
) -> None:
    c, tid, pid = wa_on
    post(c, pid, payload([audio("wamid.b2")]))
    stats = run(env, db, FakeMeta(), FakeTranscriber("parar"))
    assert stats.opt_outs == 1
    body, handled, status, _, _ = msg_row(env, tid)
    assert (body, handled, status) == ("parar", True, "done")
    assert (
        env.sql(
            "SELECT count(*) FROM suppressions WHERE tenant_id = %s AND reason = 'opt_out'", (tid,)
        )[0][0]
        == 1
    )
    assert (
        env.sql("SELECT status FROM conversations WHERE tenant_id = %s", (tid,))[0][0] == "closed"
    )


def test_failures_retry_then_fall_back_to_the_old_text(
    wa_on: tuple[TestClient, str, str], env: Env, db: Database
) -> None:
    c, tid, pid = wa_on
    post(c, pid, payload([audio("wamid.b3")]))
    meta = FakeMeta()
    meta.fail = True
    for _attempt in (1, 2):
        stats = run(env, db, meta, FakeTranscriber())
        assert stats.retry == 1 and stats.failed == 0
        assert msg_row(env, tid)[1:3] == (True, "pending")
        release_claim(env, tid)
    stats = run(env, db, meta, FakeTranscriber())
    assert stats.failed == 1
    assert msg_row(env, tid) == ("[mensagem do tipo audio]", False, "failed", None, 3)


def test_claim_window_prevents_double_processing(
    wa_on: tuple[TestClient, str, str], env: Env, db: Database
) -> None:
    c, tid, pid = wa_on
    post(c, pid, payload([audio("wamid.b4")]))
    meta = FakeMeta()
    meta.fail = True
    run(env, db, meta, FakeTranscriber())
    # logo em seguida a mensagem ainda está reservada: outro worker não a pega
    assert run(env, db, meta, FakeTranscriber()).retry == 0
    assert msg_row(env, tid)[4] == 1


@pytest.mark.parametrize(
    ("mime", "size"),
    [("video/mp4", None), ("audio/ogg", 5_000_000), ("application/pdf", None)],
)
def test_unsupported_or_oversized_audio_never_reaches_the_model(
    wa_on: tuple[TestClient, str, str], env: Env, db: Database, mime: str, size: int | None
) -> None:
    c, tid, pid = wa_on
    post(c, pid, payload([audio("wamid.b5")]))
    tr = FakeTranscriber()
    meta = FakeMeta(mime=mime, size=size)
    for _ in range(3):
        run(env, db, meta, tr, max_bytes=1_000_000)
        release_claim(env, tid)
    assert tr.calls == 0
    assert msg_row(env, tid)[:3] == ("[mensagem do tipo audio]", False, "failed")


def test_empty_transcription_counts_as_failure(
    wa_on: tuple[TestClient, str, str], env: Env, db: Database
) -> None:
    c, tid, pid = wa_on
    post(c, pid, payload([audio("wamid.b6")]))
    for _ in range(3):
        run(env, db, FakeMeta(), FakeTranscriber("   "))
        release_claim(env, tid)
    assert msg_row(env, tid)[:3] == ("[mensagem do tipo audio]", False, "failed")


def test_each_message_uses_its_own_tenant_token(
    wa_on: tuple[TestClient, str, str], env: Env, db: Database
) -> None:
    c, tid, pid = wa_on
    post(c, pid, payload([audio("wamid.b7")]))
    env.sql("UPDATE messages SET tenant_id = tenant_id WHERE tenant_id = %s", (tid,))
    meta = FakeMeta()
    run(env, db, meta, FakeTranscriber())
    assert meta.tokens and all(t == f"Bearer {TOKEN}" for t in meta.tokens)


# ------------------------------------------------------------------ rede


def test_download_refuses_http_redirects_and_oversize() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "https://evil.test/x"})
        if request.url.path == "/big":
            return httpx.Response(200, content=b"x" * 2000)
        if request.url.path == "/down":
            return httpx.Response(503)
        return httpx.Response(200, content=b"ok")

    meta = MetaClient(base="https://graph.test", transport=httpx.MockTransport(handler))
    assert meta.download("https://cdn.test/a", "t", max_bytes=100) == b"ok"
    with pytest.raises(MetaRejected):
        meta.download("http://cdn.test/a", "t", max_bytes=100)
    with pytest.raises((MetaRejected, MetaUncertain)):
        meta.download("https://cdn.test/redirect", "t", max_bytes=100)
    with pytest.raises(MetaRejected):
        meta.download("https://cdn.test/big", "t", max_bytes=1000)
    with pytest.raises(MetaUncertain):
        meta.download("https://cdn.test/down", "t", max_bytes=100)


def gemini(handler: Any) -> GeminiModel:
    return GeminiModel("k" * 20, transport=httpx.MockTransport(handler), retries=0)


def test_gemini_sends_the_audio_inline_and_returns_only_the_text() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers["x-goog-api-key"]
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"finishReason": "STOP", "content": {"parts": [{"text": " olá, tudo bem? "}]}}
                ],
                "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 6},
            },
        )

    out = gemini(handler).transcribe(AUDIO, "audio/ogg")
    assert out == ("olá, tudo bem?", 40, 6)
    part = seen["body"]["contents"][0]["parts"][0]["inlineData"]
    assert part["mimeType"] == "audio/ogg" and base64.b64decode(part["data"]) == AUDIO
    assert "responseSchema" not in seen["body"]["generationConfig"]
    assert seen["key"] == "k" * 20


@pytest.mark.parametrize(
    "answer",
    [
        {"promptFeedback": {"blockReason": "SAFETY"}},
        {"candidates": []},
        {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]},
    ],
)
def test_gemini_refusals_raise(answer: dict[str, Any]) -> None:
    with pytest.raises(AiModelError):
        gemini(lambda r: httpx.Response(200, json=answer)).transcribe(AUDIO, "audio/ogg")


# ------------------------------------------------------------------ worker


def test_worker_step_is_off_by_default(env: Env, db: Database) -> None:
    import logging

    off = Settings(**{**env.settings().model_dump(), "voice_transcription": False})
    assert cli._voice_step(db, env.box, off, object(), logging.getLogger("t")) == {}  # type: ignore[arg-type]
    on = Settings(**{**env.settings().model_dump(), "voice_transcription": True})
    # modelo sem `transcribe` (IA indisponível): nada a fazer
    assert cli._voice_step(db, env.box, on, object(), logging.getLogger("t")) == {}  # type: ignore[arg-type]
