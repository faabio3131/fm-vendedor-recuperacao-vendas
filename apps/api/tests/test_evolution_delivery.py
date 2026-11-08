"""Read-only confirmation of ambiguous sends: no duplicate WhatsApp messages."""

from __future__ import annotations

import time
from typing import Any, cast

import pytest

from fm_seller.channels.evolution_delivery import find_confirmed_outbound
from fm_seller.channels.evolution_runtime import EvolutionSender
from fm_seller.errors import AppError
from fm_seller.recovery.senders import SendError

INSTANCE = "fmav-" + "a" * 32
PHONE = "5511999991234"
BODY = "Para contratar, use este link seguro: https://example.test/checkout"


def _record(
    *,
    text: str = BODY,
    phone: str = PHONE,
    from_me: bool = True,
    provider_id: str = "3EB0ABC123",
    seconds_ago: int = 2,
) -> dict[str, Any]:
    return {
        "key": {
            "remoteJid": f"{phone}@s.whatsapp.net",
            "fromMe": from_me,
            "id": provider_id,
        },
        "message": {"extendedTextMessage": {"text": text}},
        "messageTimestamp": int(time.time()) - seconds_ago,
    }


class FakeEvolution:
    def __init__(
        self,
        *,
        response: dict[str, Any] | None = None,
        records: list[dict[str, Any]] | None = None,
        error: AppError | None = None,
    ) -> None:
        self.response = response if response is not None else {}
        self.records = records if records is not None else [_record()]
        self.error = error
        self.sent = 0
        self.lookups = 0

    def state(self, instance: str) -> str:
        assert instance == INSTANCE
        return "open"

    def _call(self, method: str, route: str, **kwargs: Any) -> dict[str, Any]:
        if route.startswith("/message/sendText/"):
            self.sent += 1
            if self.error is not None:
                raise self.error
            return self.response
        assert method == "POST" and route == f"/chat/findMessages/{INSTANCE}"
        assert kwargs["body"]["where"]["key"]["remoteJid"] == f"{PHONE}@s.whatsapp.net"
        self.lookups += 1
        return {"messages": {"records": self.records}}


def test_exact_evolution_proof_returns_provider_id_without_resending() -> None:
    client = FakeEvolution(response={})
    sender = EvolutionSender(cast(Any, client))
    assert sender.send_text({"instance_name": INSTANCE}, PHONE, BODY) == "3EB0ABC123"
    assert client.sent == 1
    assert client.lookups == 1


def test_network_uncertainty_uses_lookup_but_never_retries_send() -> None:
    client = FakeEvolution(error=AppError(503, "evolution_uncertain", "timeout"))
    sender = EvolutionSender(cast(Any, client))
    assert sender.send_text({"instance_name": INSTANCE}, PHONE, BODY) == "3EB0ABC123"
    assert client.sent == 1
    assert client.lookups == 1


def test_definite_provider_rejection_does_not_query_or_resend() -> None:
    client = FakeEvolution(error=AppError(502, "evolution_rejected", "rejected"))
    sender = EvolutionSender(cast(Any, client))
    with pytest.raises(SendError):
        sender.send_text({"instance_name": INSTANCE}, PHONE, BODY)
    assert client.sent == 1 and client.lookups == 0


@pytest.mark.parametrize(
    "records",
    [
        [_record(text="Texto diferente")],
        [_record(phone="5511888887777")],
        [_record(from_me=False)],
        [_record(seconds_ago=1000)],
        [_record(provider_id="A"), _record(provider_id="B")],
    ],
)
def test_ambiguous_or_foreign_provider_evidence_is_not_accepted(
    records: list[dict[str, Any]],
) -> None:
    client = FakeEvolution(records=records)
    assert (
        find_confirmed_outbound(
            cast(Any, client),
            INSTANCE,
            PHONE,
            BODY,
            sent_at=time.time(),
        )
        is None
    )
    assert client.sent == 0 and client.lookups == 1
