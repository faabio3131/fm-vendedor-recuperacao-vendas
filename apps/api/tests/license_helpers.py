"""Ajudantes dos testes de licença do Command. Payloads SINTÉTICOS no contrato `fmcc.license.v1`."""

from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from fm_seller.api.app import create_app
from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.licensing import signature
from tests.conftest import ORIGIN, Env

PRODUCT = "ATENDEVENDEIA"
KID = "k1"
SECRET = "segredo-de-teste-do-command-" + "x" * 16  # 32+ caracteres, sintético
API_TOKEN = "token-de-leitura-de-teste-" + "y" * 16
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def uuid7() -> str:
    """UUIDv7 sintético (Python 3.12 ainda não tem): 48 bits de tempo e o resto aleatório."""
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    rand_a, rand_b = rand >> 68, rand & ((1 << 62) - 1)  # 12 e 62 bits
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=value))


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class Lic:
    """Uma licença sintética; `body()` gera o corpo do evento ou do item da API."""

    def __init__(self, **kw: Any) -> None:
        self.customer_id = kw.get("customer_id") or uuid7()
        self.subscription_id = kw.get("subscription_id") or uuid7()
        self.license_id = kw.get("license_id") or uuid7()
        self.plan_code = kw.get("plan_code", "plano-teste")
        self.version = kw.get("version", 1)
        self.state = kw.get("state", "active")
        self.valid_from = kw.get("valid_from", NOW - timedelta(days=1))
        self.valid_until = kw.get("valid_until", NOW + timedelta(days=29))
        self.grace_ends_at = kw.get("grace_ends_at")
        self.admin_email = kw.get("admin_email", f"adm-{uuid.uuid4().hex[:8]}@example.test")
        self.authorized = kw.get("authorized", True)
        self.gateway = kw.get("gateway", "")
        self.external_ref = kw.get("external_ref", "")
        self.product = kw.get("product", PRODUCT)

    def at(self, **kw: Any) -> Lic:
        out = Lic(**{**self.__dict__, **{"authorized": self.authorized}})
        out.customer_id, out.subscription_id, out.license_id = (
            self.customer_id,
            self.subscription_id,
            self.license_id,
        )
        out.plan_code, out.version, out.state = self.plan_code, self.version, self.state
        out.valid_from, out.valid_until, out.grace_ends_at = (
            self.valid_from,
            self.valid_until,
            self.grace_ends_at,
        )
        out.admin_email, out.gateway, out.external_ref = (
            self.admin_email,
            self.gateway,
            self.external_ref,
        )
        out.product = self.product
        for k, v in kw.items():
            setattr(out, k, v)
        return out

    def item(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema_version": "fmcc.license.v1",
            "product_code": self.product,
            "customer_id": self.customer_id,
            "subscription_id": self.subscription_id,
            "license": {
                "license_id": self.license_id,
                "license_version": self.version,
                "state": self.state,
                "plan_code": self.plan_code,
                "valid_from": iso(self.valid_from),
                "valid_until": iso(self.valid_until),
                "grace_ends_at": iso(self.grace_ends_at) if self.grace_ends_at else None,
            },
            "provisioning": {
                "authorized": self.authorized,
                "admin_email": self.admin_email,
                "admin_name": "Responsável Teste",
                "account_name": "Loja Teste",
            },
        }
        if self.gateway:
            out["source"] = {
                "kind": "gateway",
                "gateway": self.gateway,
                "external_subscription_ref": self.external_ref,
            }
        return out

    def event(
        self, type_: str = "license.activated", event_id: str | None = None
    ) -> dict[str, Any]:
        return {
            **self.item(),
            "event_id": event_id or f"evt-{uuid.uuid4().hex}",
            "type": type_,
            "occurred_at": iso(NOW),
            "issuer": "fmcommand",
        }


def settings_for(env: Env, **over: Any) -> Settings:
    base = env.settings().model_dump()
    base.update(
        fmcommand_mode="enforce",
        fmcommand_product_code=PRODUCT,
        fmcommand_webhook_secrets=f"{KID}:{SECRET}",
        fmcommand_api_base_url="https://command.example.test",
        fmcommand_api_token=API_TOKEN,
    )
    base.update(over)
    return Settings(**base)


@contextmanager
def license_client(env: Env, db: Database, **over: Any) -> Iterator[TestClient]:
    with TestClient(
        create_app(settings_for(env, **over), db=db, box=env.box), headers={"Origin": ORIGIN}
    ) as c:
        yield c


def post_event(
    client: TestClient,
    body: dict[str, Any],
    *,
    secret: str = SECRET,
    kid: str = KID,
    t: int | None = None,
    event_id_header: str | None = None,
    raw: bytes | None = None,
) -> Any:
    payload = raw if raw is not None else json.dumps(body).encode()
    header = signature.sign(secret, payload, t=int(time.time()) if t is None else t, kid=kid)
    return client.post(
        "/v1/platform/webhooks/fmcommand",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-FMCC-Signature": header,
            "X-FMCC-Event-Id": event_id_header
            if event_id_header is not None
            else str(body.get("event_id", "")),
        },
    )


def random_secret() -> str:
    return secrets.token_urlsafe(32)
