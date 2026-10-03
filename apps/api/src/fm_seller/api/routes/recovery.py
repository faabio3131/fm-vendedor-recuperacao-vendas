from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from fm_seller.api.deps import get_db, get_principal
from fm_seller.recovery.opportunities import OpportunityService
from fm_seller.recovery.service import RecoveryService
from fm_seller.services import Principal

router = APIRouter(prefix="/recovery", tags=["recuperação"])
Who = Annotated[Principal, Depends(get_principal)]


def svc(request: Request) -> RecoveryService:
    return RecoveryService(get_db(request))


Svc = Annotated[RecoveryService, Depends(svc)]


def osvc(request: Request) -> OpportunityService:
    return OpportunityService(get_db(request))


OppSvc = Annotated[OpportunityService, Depends(osvc)]


class SettingsIn(BaseModel):
    timezone: str | None = Field(default=None, max_length=64)
    quiet_start: int | None = Field(default=None, ge=0, le=23)
    quiet_end: int | None = Field(default=None, ge=0, le=23)
    daily_cap: int | None = Field(default=None, ge=1, le=10)
    max_contacts_per_case: int | None = Field(default=None, ge=1, le=10)
    recovery_enabled: bool | None = None
    consent_declared: bool | None = None
    cold_enabled: bool | None = None
    cold_after_hours: int | None = Field(default=None, ge=1, le=48)


class SequenceIn(BaseModel):
    enabled: bool = True
    steps: list[dict[str, Any]] = Field(max_length=10)


class TemplateIn(BaseModel):
    body: str = Field(max_length=1024)


class TemplateStatusIn(BaseModel):
    status: str = Field(max_length=20)


class SuppressionIn(BaseModel):
    identity: str = Field(max_length=200)


@router.get("/summary")
def summary(s: Svc, p: Who, days: Annotated[int, Query(ge=1, le=365)] = 30) -> dict[str, Any]:
    return s.summary(p, days)


@router.get("/cases")
def cases(
    s: Svc,
    p: Who,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[dict[str, Any]]:
    return s.cases(p, status, limit)


@router.get("/settings")
def get_settings(s: Svc, p: Who) -> dict[str, Any]:
    return s.get_settings(p)


@router.put("/settings")
def put_settings(body: SettingsIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.put_settings(p, body.model_dump(exclude_none=True))


@router.get("/sequences")
def sequences(s: Svc, p: Who) -> list[dict[str, Any]]:
    return s.sequences(p)


@router.put("/sequences/{trigger}")
def put_sequence(trigger: str, body: SequenceIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.put_sequence(p, trigger, body.enabled, body.steps)


@router.delete("/sequences/{trigger}")
def reset_sequence(trigger: str, s: Svc, p: Who) -> dict[str, str]:
    s.reset_sequence(p, trigger)
    return {"status": "default"}


@router.get("/templates")
def templates(s: Svc, p: Who) -> list[dict[str, Any]]:
    return s.templates(p)


@router.put("/templates/{key}")
def put_template(key: str, body: TemplateIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.put_template(p, key, body.body)


@router.post("/templates/{key}/status")
def template_status(key: str, body: TemplateStatusIn, s: Svc, p: Who) -> dict[str, Any]:
    return s.set_template_status(p, key, body.status)


@router.get("/suppressions")
def suppressions(s: Svc, p: Who) -> list[dict[str, Any]]:
    return s.suppressions(p)


@router.post("/suppressions")
def add_suppression(body: SuppressionIn, s: Svc, p: Who) -> dict[str, str]:
    return s.add_suppression(p, body.identity)


class OpportunityIn(BaseModel):
    name: str = Field(default="", max_length=200)
    phone: str = Field(max_length=40)
    product: str = Field(default="", max_length=200)
    amount_cents: int = Field(default=0, ge=0)
    payment_url: str | None = Field(default=None, max_length=500)
    note: str = Field(default="", max_length=300)
    contact_authorized: bool = False


class ImportIn(BaseModel):
    csv: str = Field(max_length=300_000)
    contact_authorized: bool = False


class OutcomeIn(BaseModel):
    outcome: str = Field(max_length=10)
    amount_cents: int | None = Field(default=None, ge=0)


@router.post("/opportunities", status_code=201)
def create_opportunity(body: OpportunityIn, s: OppSvc, p: Who) -> dict[str, Any]:
    return s.create(
        p,
        name=body.name,
        phone=body.phone,
        product=body.product,
        amount_cents=body.amount_cents,
        payment_url=body.payment_url,
        note=body.note,
        authorized=body.contact_authorized,
    )


@router.post("/opportunities/import")
def import_opportunities(body: ImportIn, s: OppSvc, p: Who) -> dict[str, Any]:
    return s.import_csv(p, body.csv, body.contact_authorized)


@router.post("/cases/{case_id}/outcome")
def case_outcome(case_id: uuid.UUID, body: OutcomeIn, s: OppSvc, p: Who) -> dict[str, Any]:
    return s.set_outcome(p, case_id, body.outcome, body.amount_cents)
