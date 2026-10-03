"""Operações de administração: migrations, chave de cifragem e criação manual de cliente.

A criação manual serve até o recebimento de compras (Cakto/Hotmart) entrar em operação.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime, timedelta

import psycopg

from fm_seller.config import get_settings
from fm_seller.db import Database
from fm_seller.events.ingest import reprocess_failed
from fm_seller.logging_setup import setup_logging
from fm_seller.migrate import apply_migrations
from fm_seller.provisioning.platform import reprocess_platform
from fm_seller.recovery.engine import handle_event, run_due_steps
from fm_seller.recovery.senders import build_sender
from fm_seller.security.crypto import SecretBox


def create_tenant(admin_url: str, name: str, email: str, plan: str, role: str = "owner") -> str:
    """Cria cliente, plano e convite para o e-mail. Usa o papel dono (admin), nunca o do app."""
    with psycopg.connect(admin_url) as conn:
        conn.execute("SELECT set_config('app.system', 'on', true)")
        tenant = conn.execute(
            "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (name,)
        ).fetchone()
        assert tenant is not None
        tenant_id = tenant[0]
        conn.execute(
            "INSERT INTO tenant_plans (tenant_id, plan_key, source) VALUES (%s, %s, 'manual')",
            (tenant_id, plan),
        )
        conn.execute(
            "INSERT INTO pending_invites (tenant_id, email, role, expires_at) "
            "VALUES (%s, lower(%s), %s, %s)",
            (tenant_id, email, role, datetime.now(UTC) + timedelta(days=30)),
        )
        conn.execute(
            "INSERT INTO audit_log (tenant_id, action, target) VALUES (%s, 'tenant.created', %s)",
            (tenant_id, email),
        )
        conn.commit()
    return str(tenant_id)


def map_product(admin_url: str, provider: str, product_id: str, plan: str) -> None:
    """Liga um produto vendido na Cakto/Hotmart a um plano (dado, não código)."""
    with psycopg.connect(admin_url) as conn:
        conn.execute(
            "INSERT INTO plan_products (provider, external_product_id, plan_key) "
            "VALUES (%s, %s, %s) ON CONFLICT (provider, external_product_id) "
            "DO UPDATE SET plan_key = EXCLUDED.plan_key",
            (provider, product_id, plan),
        )
        conn.commit()


def run_worker(interval: int, once: bool) -> None:
    """Repassa eventos que falharam e envia os passos de recuperação devidos."""
    settings = get_settings()
    setup_logging()
    log = logging.getLogger("fm_seller.worker")
    db = Database(settings.database_url)
    db.open()
    box = SecretBox(settings.secrets_keys)
    sender = build_sender(settings.env)
    try:
        while True:
            redone = reprocess_failed(db, box, handle_event)
            redone_platform = reprocess_platform(db, box)
            stats = run_due_steps(db, box, sender)
            log.info(
                "ciclo",
                extra={
                    "ctx": {"reprocessados": redone, "compras": redone_platform, **stats.__dict__}
                },
            )
            if once:
                return
            time.sleep(interval)
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fm-seller")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="Aplica as migrations pendentes")
    sub.add_parser("gen-key", help="Gera uma chave de cifragem (FM_SECRETS_KEYS)")
    ct = sub.add_parser("create-tenant", help="Cria um cliente e convida o e-mail do dono")
    ct.add_argument("--name", required=True)
    ct.add_argument("--email", required=True)
    ct.add_argument("--plan", default="fase-1")
    mp = sub.add_parser("map-product", help="Liga um produto da Cakto/Hotmart a um plano")
    mp.add_argument("--provider", required=True, choices=["cakto", "hotmart"])
    mp.add_argument("--product-id", required=True)
    mp.add_argument("--plan", required=True)
    wk = sub.add_parser("worker", help="Envia passos de recuperação devidos e reprocessa falhas")
    wk.add_argument("--interval", type=int, default=30, help="segundos entre ciclos")
    wk.add_argument("--once", action="store_true", help="roda um ciclo e sai")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.cmd == "migrate":
        applied = apply_migrations(settings.database_admin_url)
        print("Aplicadas:", ", ".join(applied) if applied else "nenhuma (já em dia)")
    elif args.cmd == "gen-key":
        print(SecretBox.generate_key_spec())
    elif args.cmd == "create-tenant":
        tenant_id = create_tenant(settings.database_admin_url, args.name, args.email, args.plan)
        print("Cliente criado:", tenant_id)
    elif args.cmd == "map-product":
        map_product(settings.database_admin_url, args.provider, args.product_id, args.plan)
        print("Produto ligado ao plano.")
    elif args.cmd == "worker":
        run_worker(args.interval, args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
