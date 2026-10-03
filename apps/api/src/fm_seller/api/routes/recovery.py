from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from fm_seller.api.deps import get_db, get_principal
from fm_seller.recovery.service import RecoveryService
from fm_seller.services import Principal

router = APIRouter(prefix="/recovery", tags=["recuperação"])
Who = Annotated[Principal, Depends(get_principal)]


def svc(request: Request) -> RecoveryService:
    return RecoveryService(get_db(request))


Svc = Annotated[RecoveryService, Depends(svc)]


class SettingsIn(BaseModel):
    timezone: str | None = Field(default=None, max_length=64)
    quiet_start: int | None = Field(default=None, ge=0, le=23)
    quiet_end: int | None = Field(default=None, ge=0, le=23)
    daily_cap: int | None = Field(default=None, ge=1, le=10)
    max_contacts_per_case: int | None = Field(default=None, ge=1, le=10)
    recovery_enabled: bool | None = None
    consent_declared: bool | None = None


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
