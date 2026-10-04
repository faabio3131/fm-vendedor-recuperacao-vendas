"""Conferência antes de subir: configuração, chave de cifragem, banco, papéis e isolamento.

`python -m fm_seller.cli preflight` lê a configuração do ambiente (a mesma que a API vai usar) e
termina com 0 (tudo certo), 1 (só avisos) ou 2 (há algo crítico: não suba). Nunca imprime segredo,
senha nem URL de banco com senha. Não substitui os testes com contas reais: confere que o
ambiente está montado do jeito que o código espera, não que o Google, a Meta ou a Cakto funcionam.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import psycopg
from pydantic import ValidationError

from fm_seller.config import Settings
from fm_seller.ops.health import CRITICAL, WARNING, Finding, pending_migrations
from fm_seller.security.crypto import CryptoError, SecretBox

PLACEHOLDER_CLIENT = "pendente."
CONNECT_TIMEOUT = 8


@dataclass
class Report:
    passed: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def ok(self, message: str) -> None:
        self.passed.append(message)

    def bad(self, level: str, code: str, message: str) -> None:
        self.findings.append(Finding(level, code, message))


def db_user(url: str) -> str:
    return urlsplit(url).username or ""


def role_findings(row: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Problemas do papel do app (linha de `pg_roles`): (código, mensagem). Função pura."""
    if row is None:
        return [("app_role_missing", "O papel do app não foi encontrado no banco.")]
    out: list[tuple[str, str]] = []
    if row["rolsuper"]:
        out.append(("app_role_superuser", "O papel do app é superusuário: o isolamento não vale."))
    if row["rolbypassrls"]:
        out.append(("app_role_bypassrls", "O papel do app ignora RLS: o isolamento não vale."))
    return out


def _settings_problems(settings: Settings, report: Report) -> None:
    if settings.env in ("dev", "test"):
        report.bad(
            WARNING,
            "env_dev",
            f"FM_ENV={settings.env}: o preflight serve para staging ou produção "
            "(em dev vale o login simulado e os padrões de desenvolvimento).",
        )
    else:
        report.ok(f"FM_ENV={settings.env}")
    try:
        SecretBox(settings.secrets_keys)
        report.ok("Chave de cifragem no formato certo (id:base64, 32 bytes)")
    except CryptoError as exc:
        report.bad(CRITICAL, "secrets_key", f"FM_SECRETS_KEYS inválida: {exc}")
    if settings.google_client_id.startswith(PLACEHOLDER_CLIENT):
        level = CRITICAL if settings.env == "prod" else WARNING
        report.bad(
            level,
            "google_placeholder",
            "FM_GOOGLE_CLIENT_ID ainda é o valor provisório: o login com Google não funciona.",
        )
    elif settings.google_client_id:
        report.ok("Client ID do Google definido")
    if db_user(settings.database_url) == db_user(settings.database_admin_url):
        report.bad(
            CRITICAL,
            "same_db_user",
            "A API e as migrations usam o MESMO usuário de banco: use o papel fm_app na API.",
        )
    else:
        report.ok("API e migrations usam usuários de banco diferentes")
    if not settings.ai_api_key.get_secret_value():
        report.bad(
            WARNING,
            "ai_key_missing",
            "FM_AI_API_KEY vazia: as conversas ficam com uma pessoa (vendedor IA desligado).",
        )
    if not (settings.platform_cakto_secret or settings.platform_hotmart_hottok):
        report.bad(
            WARNING,
            "platform_secrets_missing",
            "Sem segredo de Cakto/Hotmart da plataforma: a compra do SaaS não cria conta sozinha.",
        )
    if settings.capture_events:
        report.bad(
            WARNING,
            "capture_on",
            "FM_CAPTURE_EVENTS está ligada: desligue depois de capturar os eventos de teste.",
        )
    if settings.whatsapp_live:
        report.bad(
            WARNING,
            "whatsapp_live",
            "FM_WHATSAPP_LIVE está ligado: envio real habilitado. Confirme que já foi validado "
            "com conta real (docs/LANCAMENTO_MVP.md, seção 4).",
        )
    else:
        report.ok("Envio real pelo WhatsApp desligado (padrão seguro)")


def _rls_gaps(admin_url: str) -> list[str]:
    with psycopg.connect(admin_url, connect_timeout=CONNECT_TIMEOUT) as conn:
        return [r[0] for r in conn.execute(_RLS_SQL).fetchall()]


_RLS_SQL = """
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind = 'r'
   AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid
               AND a.attname = 'tenant_id' AND NOT a.attisdropped)
   AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
 ORDER BY c.relname
"""


def _database_problems(settings: Settings, report: Report) -> None:
    try:
        with psycopg.connect(settings.database_admin_url, connect_timeout=CONNECT_TIMEOUT) as c:
            c.execute("SELECT 1")
        report.ok("Banco acessível com o papel das migrations")
    except psycopg.Error as exc:
        report.bad(
            CRITICAL,
            "db_admin_unreachable",
            f"Banco inacessível (migrations): {type(exc).__name__}",
        )
        return
    try:
        with psycopg.connect(settings.database_url, connect_timeout=CONNECT_TIMEOUT) as conn:
            row = conn.execute(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            ).fetchone()
        info = None if row is None else {"rolsuper": row[0], "rolbypassrls": row[1]}
        problems = role_findings(info)
        for code, message in problems:
            report.bad(CRITICAL, code, message)
        if not problems:
            report.ok("Papel do app sem superusuário e sem bypass de RLS")
    except psycopg.Error as exc:
        report.bad(CRITICAL, "db_app_unreachable", f"Banco inacessível (API): {type(exc).__name__}")
    pending = pending_migrations(settings.database_admin_url)
    if pending:
        report.bad(
            CRITICAL,
            "migrations_pending",
            "Migrations pendentes: "
            + ", ".join(pending)
            + " (rode `cli migrate` ou `cli bootstrap`).",
        )
    else:
        report.ok("Migrations em dia")
    gaps = _rls_gaps(settings.database_admin_url)
    if gaps:
        report.bad(
            CRITICAL,
            "rls_not_forced",
            "Tabelas com tenant_id sem RLS forçada: " + ", ".join(gaps),
        )
    else:
        report.ok("RLS forçada em toda tabela com tenant_id")


def run(
    load_settings: Callable[[], Settings],
    *,
    check_database: bool = True,
) -> Report:
    report = Report()
    try:
        settings = load_settings()
    except ValidationError as exc:
        # Só o motivo e os nomes dos campos (`msg` e `loc`); nunca `input`, que é o valor.
        parts = "; ".join(
            sorted(
                {
                    (".".join(str(p) for p in e["loc"]) + ": " if e["loc"] else "")
                    + str(e["msg"]).removeprefix("Value error, ")[:400]
                    for e in exc.errors()
                }
            )
        )
        report.bad(CRITICAL, "settings_invalid", f"Configuração insegura ou incompleta: {parts}")
        return report
    report.ok("Configuração carregada e aceita pela validação de staging/produção")
    _settings_problems(settings, report)
    if check_database:
        _database_problems(settings, report)
    return report
