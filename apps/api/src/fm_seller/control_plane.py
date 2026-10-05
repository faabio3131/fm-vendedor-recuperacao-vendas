"""Conexão com o FM Command (centro de controle da F&M): leitura agregada, só por serviço.

O FM Command puxa daqui o estado comercial e operacional do AtendeVendeIA por HTTP, no desenho do
conector do Kordena (`/v1/control-plane/fmcc/health` e `/snapshot`, com token de serviço).
Regras, todas testadas:
- **só leitura e só agregados**: contagens e fatos com identificador opaco; nunca conversa,
  contato, telefone, e-mail, nome de cliente, credencial nem texto livre;
- **desligado por padrão**: sem `FM_FMCC_CONTROL_PLANE_TOKEN` (mínimo 32 caracteres) as rotas
  respondem 404;
- **token de serviço** comparado em tempo constante; sessão de pessoa não vale aqui;
- **o que não existe aqui não vira zero**: o mapa `coverage` diz o que é fonte real, o que é
  parcial e o que não está disponível (cobrança, pagamentos, custos, leads, suporte).
Exceção documentada à regra 4 do AGENTS.md (ler todos os clientes em modo `system`): ADR-0004.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

from fm_seller import platform_admin as pa
from fm_seller.config import Settings
from fm_seller.db import Conn
from fm_seller.errors import not_found, unauthorized

SCHEMA_VERSION = "atendevendeia.fmcc.v1"
PRODUCT_CODE = "ATENDEVENDEIA"
MIN_TOKEN_CHARS = 32

# O que este produto sabe e o que não sabe informar ao FM Command. Ausência não é zero.
COVERAGE: dict[str, str] = {
    "subscriptions": "available: estado do plano de cada cliente (ativo, atraso, suspenso...)",
    "operations": "partial: contagens do sistema (worker, fila, falhas), sem telemetria externa",
    "ai_usage": "partial: respostas e falhas da IA por dia; sem custo em dinheiro",
    "trials": "unavailable: o produto não tem teste gratuito próprio",
    "billing_invoices": "unavailable: a cobrança é da plataforma de venda (Cakto/Hotmart)",
    "payments": "unavailable: a cobrança é da plataforma de venda (Cakto/Hotmart)",
    "receivables": "unavailable: a cobrança é da plataforma de venda (Cakto/Hotmart)",
    "infrastructure_cost": "unavailable: sem fonte de custo ligada",
    "operating_cost": "unavailable: sem fonte de custo ligada",
    "leads": "unavailable: sem CRM",
    "support": "unavailable: sem sistema de suporte",
    "internal_test_tenants": "unavailable: clientes de teste não têm marca (no staging contam)",
}


def enabled(cfg: Settings) -> bool:
    return len(cfg.fmcc_control_plane_token) >= MIN_TOKEN_CHARS


def authorize(cfg: Settings, authorization: str | None) -> None:
    """404 se a conexão está desligada; 401 se o token faltar ou estiver errado."""
    if not enabled(cfg):
        raise not_found()
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized("Token de serviço ausente.")
    # Compara digests de tamanho fixo: tempo constante e sem vazar o tamanho do token.
    given = hashlib.sha256(token.strip().encode()).digest()
    expected = hashlib.sha256(cfg.fmcc_control_plane_token.encode()).digest()
    if not hmac.compare_digest(given, expected):
        raise unauthorized("Token de serviço inválido.")


def subscription_facts(conn: Conn) -> list[dict[str, Any]]:
    """Fatos canônicos de assinatura: id opaco (cliente + estado), sem nome nem e-mail."""
    rows = conn.execute(
        "SELECT tenant_id, plan_key, status, status_since FROM tenant_plans "
        "WHERE status IN ('active', 'canceled') ORDER BY tenant_id"
    ).fetchall()
    facts: list[dict[str, Any]] = []
    for r in rows:
        kind = "subscription.active" if r["status"] == "active" else "subscription.cancelled"
        suffix = "active" if r["status"] == "active" else "cancelled"
        facts.append(
            {
                "external_id": f"sub:{r['tenant_id']}:{suffix}",
                "fact_type": kind,
                "payload": {
                    "tenant_id": str(r["tenant_id"]),
                    "plan_key": r["plan_key"],
                    "status": r["status"],
                    "product_code": PRODUCT_CODE,
                },
                "source_timestamp": r["status_since"].isoformat(),
            }
        )
    return facts


def build_snapshot(conn: Conn, *, now: datetime | None = None) -> dict[str, Any]:
    stamp = now or datetime.now(UTC)
    operations = pa.metrics(conn, now=stamp)
    by_status: dict[str, int] = operations["clients"]["by_plan_status"]
    summary = {
        "customers": int(operations["clients"]["total"]),
        "active_subscriptions": by_status.get("active", 0),
        "past_due_subscriptions": by_status.get("past_due", 0),
        "suspended_subscriptions": by_status.get("suspended", 0),
        "canceled_subscriptions": by_status.get("canceled", 0),
        "refunded_subscriptions": by_status.get("refunded", 0),
        "deletion_pending": int(operations["clients"]["deletion_pending"]),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "product_code": PRODUCT_CODE,
        "as_of": stamp.isoformat(),
        "summary": summary,
        "facts": subscription_facts(conn),
        "operations": {k: v for k, v in operations.items() if k not in ("generated_at", "clients")},
        "coverage": COVERAGE,
    }
