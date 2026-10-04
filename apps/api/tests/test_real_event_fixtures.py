"""Fixtures de eventos reais (ou de exemplo) conferidas contra o normalizador.

Para trazer um evento real: tests/fixtures/real_events/README.md. Fixture `sintetico` NÃO prova o
formato real; só `captura_anonimizada` prova.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fm_seller.events import capture, compare
from fm_seller.events.normalize import NORMALIZERS

ROOT = Path(__file__).parent / "fixtures" / "real_events"
FILES = sorted(ROOT.glob("*/*.json"))
PII = re.compile(r"[\w.+-]+@(?!example\.test)[\w-]+\.[\w.]+")


def load(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def test_fixture_folder_and_readme_exist() -> None:
    assert (ROOT / "README.md").exists()
    assert {p.parent.name for p in FILES} <= set(NORMALIZERS)


def test_every_fixture_matches_what_the_normalizer_expects() -> None:
    for path in FILES:
        fx = load(path)
        provider, payload, expect = fx["provider"], fx["payload"], fx.get("expect", {})
        assert provider == path.parent.name, path
        assert fx["origem"] in ("sintetico", "captura_anonimizada"), path
        report = compare.compare(provider, payload)
        assert report["known_event"] is expect.get("known_event", True), path
        event = NORMALIZERS[provider](payload)
        if expect.get("kind") is not None:
            assert event is not None and event.kind == expect["kind"], path
        if event is not None:
            if "has_email" in expect:
                assert (event.email is not None) is expect["has_email"], path
            if "has_phone" in expect:
                assert (event.phone is not None) is expect["has_phone"], path
            if "amount_cents" in expect:
                assert event.amount_cents == expect["amount_cents"], path


def test_no_fixture_carries_secrets_or_real_looking_personal_data() -> None:
    for path in FILES:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert capture.redact(data["payload"]) == data["payload"] or all(
            v in ("", None) for v in _secret_values(data["payload"])
        ), f"segredo em {path}"
        assert not PII.search(raw), f"e-mail que não é de exemplo em {path}"
        assert '"hottok"' not in raw.lower() and "bearer " not in raw.lower(), path


def _secret_values(value: Any) -> list[Any]:
    out: list[Any] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if capture._is_secret_key(str(k)):
                out.append(v)
            out.extend(_secret_values(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_secret_values(v))
    return out
