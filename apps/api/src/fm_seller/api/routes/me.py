from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from fm_seller.api.deps import get_db, get_principal
from fm_seller.providers.catalog import PROVIDERS
from fm_seller.services import Principal, tenant_features

router = APIRouter(tags=["conta"])


@router.get("/me")
def me(request: Request, p: Annotated[Principal, Depends(get_principal)]) -> dict[str, Any]:
    plan_status, plan_key, features = tenant_features(get_db(request), p)
    return {
        "user": {"email": p.email, "name": p.name},
        "tenant": {"id": str(p.tenant_id), "role": p.role},
        "tenants": [
            {"id": str(m.tenant_id), "name": m.tenant_name, "role": m.role} for m in p.memberships
        ],
        "plan": {"key": plan_key, "status": plan_status},
        "features": features,
    }


@router.get("/providers")
def providers(
    request: Request, p: Annotated[Principal, Depends(get_principal)]
) -> list[dict[str, Any]]:
    """Catálogo que desenha a central de conexões. `enabled` depende do plano."""
    _, _, features = tenant_features(get_db(request), p)
    return [
        {
            "key": prov.key,
            "name": prov.name,
            "group": prov.group,
            "phase": prov.phase,
            "description": prov.description,
            "webhook": prov.webhook,
            "generates_secret": prov.generates_secret,
            "enabled": prov.feature in features,
            "fields": [
                {
                    "key": f.key,
                    "label": f.label,
                    "secret": f.secret,
                    "required": f.required,
                    "help": f.help,
                }
                for f in prov.fields
            ],
        }
        for prov in PROVIDERS
    ]
