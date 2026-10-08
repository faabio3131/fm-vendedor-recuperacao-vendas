"""Contrato `fmcc.license.v1`: validação pura (sem banco, sem rede).

Espelha as regras do domínio do Command (`validateLicense` na PR #62 do FM-CONTROL-CENTER):
identificadores UUIDv7, sete estados, `valid_from < valid_until`, carência só em `past_due` e
nunca antes do fim da vigência, versão inteira maior que zero. Campos desconhecidos são ignorados;
versão de contrato desconhecida é recusada.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = "fmcc.license.v1"

STATES = ("pending", "active", "past_due", "suspended", "canceled", "refunded", "expired")
# Estado comercial do Command -> estado do plano no AtendeVendeIA (`pending` não vira estado).
STATUS_FOR_STATE: dict[str, str] = {
    "active": "active",
    "past_due": "past_due",
    "suspended": "suspended",
    "expired": "suspended",
    "canceled": "canceled",
    "refunded": "refunded",
}
# O tipo do evento descreve o motivo; o efeito vem do estado. Os dois precisam concordar.
EVENT_TYPES: dict[str, tuple[str, ...]] = {
    "license.activated": ("active",),
    "license.renewed": ("active",),
    "license.plan_changed": ("active", "past_due"),
    "license.past_due": ("past_due",),
    "license.recovered": ("active",),
    "license.suspended": ("suspended",),
    "license.canceled": ("canceled",),
    "license.refunded": ("refunded",),
    "license.expired": ("expired",),
}

_UUID_V7 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I
)
_PRODUCT = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_EVENT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_GATEWAY = re.compile(r"^[a-z0-9_]{1,32}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}$")


class ContractError(Exception):
    """Evento ou licença fora do contrato. `code` é estável; a mensagem nunca ecoa o corpo."""

    def __init__(self, code: str, status: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class License:
    license_id: uuid.UUID
    customer_id: uuid.UUID
    subscription_id: uuid.UUID
    product_code: str
    plan_code: str
    version: int
    state: str
    valid_from: datetime
    valid_until: datetime
    grace_ends_at: datetime | None


@dataclass(frozen=True)
class Provisioning:
    authorized: bool
    admin_email: str
    admin_name: str
    account_name: str


@dataclass(frozen=True)
class Source:
    kind: str
    gateway: str
    external_subscription_ref: str


@dataclass(frozen=True)
class LicenseEvent:
    event_id: str
    type: str
    occurred_at: datetime
    license: License
    provisioning: Provisioning | None
    source: Source | None


@dataclass(frozen=True)
class LicenseItem:
    """Item da API de licenças (reconciliação): a licença completa, sem dados de evento."""

    license: License
    provisioning: Provisioning | None
    source: Source | None


def is_command_id(value: object) -> bool:
    return isinstance(value, str) and bool(_UUID_V7.match(value))


def _uuid(value: object) -> uuid.UUID:
    if not is_command_id(value):
        raise ContractError("invalid_command_id")
    return uuid.UUID(str(value))


def _when(value: object, code: str) -> datetime:
    if not isinstance(value, str):
        raise ContractError(code)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(code) from exc
    if parsed.tzinfo is None:
        raise ContractError(code)
    return parsed.astimezone(UTC)


def _obj(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(code)
    return value


def parse_license(body: dict[str, Any]) -> License:
    """Lê `customer_id`, `subscription_id`, `product_code` e o objeto `license` de um corpo."""
    raw = _obj(body.get("license"), "missing_license")
    product = body.get("product_code")
    if not isinstance(product, str) or not _PRODUCT.match(product):
        raise ContractError("invalid_product_code")
    plan = raw.get("plan_code")
    if not isinstance(plan, str) or not plan.strip() or len(plan) > 64:
        raise ContractError("missing_plan")
    version = raw.get("license_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractError("invalid_version")
    state = raw.get("state")
    if not isinstance(state, str) or state not in STATES:
        raise ContractError("invalid_state")
    valid_from = _when(raw.get("valid_from"), "invalid_validity")
    valid_until = _when(raw.get("valid_until"), "invalid_validity")
    if valid_from >= valid_until:
        raise ContractError("invalid_validity")
    grace: datetime | None = None
    if raw.get("grace_ends_at") is not None:
        grace = _when(raw.get("grace_ends_at"), "invalid_grace")
        if grace < valid_until or state != "past_due":
            raise ContractError("invalid_grace")
    return License(
        license_id=_uuid(raw.get("license_id")),
        customer_id=_uuid(body.get("customer_id")),
        subscription_id=_uuid(body.get("subscription_id")),
        product_code=product,
        plan_code=plan.strip(),
        version=version,
        state=state,
        valid_from=valid_from,
        valid_until=valid_until,
        grace_ends_at=grace,
    )


def parse_provisioning(raw: object) -> Provisioning | None:
    if raw is None:
        return None
    prov = _obj(raw, "invalid_provisioning")
    authorized = prov.get("authorized")
    if not isinstance(authorized, bool):
        raise ContractError("invalid_provisioning")
    email = str(prov.get("admin_email") or "").strip().lower()
    if authorized and not _EMAIL.match(email):
        raise ContractError("invalid_provisioning")
    name = str(prov.get("admin_name") or "").strip()[:200]
    account = str(prov.get("account_name") or "").strip()[:200]
    return Provisioning(authorized, email, name, account)


def parse_source(raw: object) -> Source | None:
    if raw is None:
        return None
    src = _obj(raw, "invalid_source")
    gateway = str(src.get("gateway") or "")
    if gateway and not _GATEWAY.match(gateway):
        raise ContractError("invalid_source")
    ref = str(src.get("external_subscription_ref") or "")[:200]
    return Source(str(src.get("kind") or "")[:32], gateway, ref)


def parse_event(body: dict[str, Any]) -> LicenseEvent:
    if body.get("schema_version") != SCHEMA_VERSION:
        raise ContractError("unsupported_schema")
    event_id = body.get("event_id")
    if not isinstance(event_id, str) or not _EVENT_ID.match(event_id):
        raise ContractError("invalid_event_id")
    kind = body.get("type")
    if not isinstance(kind, str) or kind not in EVENT_TYPES:
        raise ContractError("invalid_event_type")
    occurred = _when(body.get("occurred_at"), "invalid_event")
    lic = parse_license(body)
    if lic.state not in EVENT_TYPES[kind]:
        raise ContractError("type_state_mismatch")
    return LicenseEvent(
        event_id=event_id,
        type=kind,
        occurred_at=occurred,
        license=lic,
        provisioning=parse_provisioning(body.get("provisioning")),
        source=parse_source(body.get("source")),
    )


def parse_item(body: object) -> LicenseItem:
    item = _obj(body, "invalid_item")
    if item.get("schema_version") != SCHEMA_VERSION:
        raise ContractError("unsupported_schema")
    return LicenseItem(
        license=parse_license(item),
        provisioning=parse_provisioning(item.get("provisioning")),
        source=parse_source(item.get("source")),
    )
