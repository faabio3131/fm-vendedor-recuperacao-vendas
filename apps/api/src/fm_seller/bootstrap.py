"""Primeira subida em banco gerenciado de um usuário só (ex.: Render), sem Shell nem psql.

Roda antes da API, é idempotente e cada passo só age se faltar algo:
1. cria o papel do app (`fm_app`, sem superusuário e sem bypass de RLS) se a senha foi dada;
2. aplica as migrations (precisam rodar DEPOIS do papel, pois só dão permissão a ele se existir);
3. cria o primeiro cliente, se o banco ainda não tem nenhum e o e-mail do dono foi dado.

Nada disso substitui `scripts/db/bootstrap_app_role.sql` nem a CLI: é só o mesmo caminho
automatizado para quem não tem terminal. A senha vem de variável de ambiente, nunca do código.
"""

from __future__ import annotations

import psycopg
from psycopg import sql

from fm_seller.cli import create_tenant
from fm_seller.config import Settings
from fm_seller.migrate import apply_migrations


class BootstrapError(RuntimeError):
    """Falha que o operador precisa resolver; a mensagem diz o que fazer."""


def ensure_app_role(admin_url: str, password: str, role: str = "fm_app") -> str:
    """Cria o papel do app se não existir. Devolve "created" ou "exists".

    Papel que já existe não tem a senha alterada aqui (evita derrubar a API em produção).
    """
    with psycopg.connect(admin_url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        if exists is None:
            try:
                conn.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )
            except psycopg.errors.InsufficientPrivilege as exc:
                raise BootstrapError(
                    "O usuário padrão deste banco não pode criar papéis. Não rode a API como dono "
                    "do banco (isso desliga o isolamento entre clientes): use outro Postgres "
                    "gerenciado ou peça a liberação ao suporte do provedor."
                ) from exc
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(_current_database(conn)), sql.Identifier(role)
            )
        )
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (role,)
        ).fetchone()
    if row is None or row[0] or row[1]:
        raise BootstrapError(f"O papel {role} é superusuário ou ignora RLS: recuse este banco.")
    return "exists" if exists is not None else "created"


def _current_database(conn: psycopg.Connection) -> str:
    row = conn.execute("SELECT current_database()").fetchone()
    assert row is not None
    return str(row[0])


def has_any_tenant(admin_url: str) -> bool:
    with psycopg.connect(admin_url) as conn:
        conn.execute("SELECT set_config('app.system', 'on', true)")
        return conn.execute("SELECT 1 FROM tenants LIMIT 1").fetchone() is not None


def run_bootstrap(settings: Settings) -> list[str]:
    """Executa os passos e devolve o que foi feito (sem segredos)."""
    done: list[str] = []
    password = settings.bootstrap_app_password.get_secret_value()
    if password:
        done.append(f"papel fm_app: {ensure_app_role(settings.database_admin_url, password)}")
    applied = apply_migrations(settings.database_admin_url)
    done.append("migrations: " + (", ".join(applied) if applied else "já em dia"))
    email, name = settings.bootstrap_owner_email.strip(), settings.bootstrap_tenant_name.strip()
    if email and name:
        if has_any_tenant(settings.database_admin_url):
            done.append("primeiro cliente: já existe cliente, nada a criar")
        else:
            create_tenant(settings.database_admin_url, name, email, "fase-1")
            done.append("primeiro cliente: criado (convite para o e-mail informado)")
    return done
