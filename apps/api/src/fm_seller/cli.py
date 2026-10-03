"""Operações de administração: migrations, chave de cifragem e criação manual de cliente.

A criação manual serve até o recebimento de compras (Cakto/Hotmart) entrar em operação.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta

import psycopg

from fm_seller.config import get_settings
from fm_seller.migrate import apply_migrations
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fm-seller")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="Aplica as migrations pendentes")
    sub.add_parser("gen-key", help="Gera uma chave de cifragem (FM_SECRETS_KEYS)")
    ct = sub.add_parser("create-tenant", help="Cria um cliente e convida o e-mail do dono")
    ct.add_argument("--name", required=True)
    ct.add_argument("--email", required=True)
    ct.add_argument("--plan", default="fase-1")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
