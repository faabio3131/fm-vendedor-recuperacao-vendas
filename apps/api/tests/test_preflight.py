"""Preflight (antes de subir) e smoke (ambiente no ar), mais o ensaio de subida e rollback."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fm_seller import cli
from fm_seller.api.app import create_app
from fm_seller.config import Settings, get_settings
from fm_seller.db import Database
from fm_seller.ops import preflight, smoke
from fm_seller.ops.health import CRITICAL, WARNING, exit_code
from fm_seller.security.crypto import SecretBox
from tests.conftest import Env
from tests.test_ops import needs_pg_tools

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts" / "ops"


BASE: dict[str, Any] = {
    "env": "staging",
    "database_url": "postgresql://fm_app:senha-a@db.example.test:5432/fm",
    "database_admin_url": "postgresql://fm_owner:senha-b@db.example.test:5432/fm",
    "secrets_keys": SecretBox.generate_key_spec(),
    "google_client_id": "123-abc.apps.googleusercontent.com",
    "cookie_secure": True,
    "trust_proxy": True,
    "web_origin": "https://app.example.test",
    "public_base_url": "https://api.example.test",
    "ai_api_key": "chave-de-teste",
    "platform_cakto_secret": "segredo-de-teste",
}


def staging(**over: Any) -> Settings:
    """Staging válido (passa na validação do código), com banco de mentira."""
    return Settings(**{**BASE, **over})


def staging_db(db_env: Env, **over: Any) -> Settings:
    """Staging apontando para o banco de teste de verdade. O banco de teste é `localhost` com
    senha `dev_`, que a validação de staging recusa de propósito: aqui ela é pulada só para
    poder conferir as verificações de banco."""
    fields = {
        **BASE,
        "database_url": db_env.app_url,
        "database_admin_url": db_env.admin_url,
        "secrets_keys": db_env.key_spec,
        "ai_api_key": SecretStr(BASE["ai_api_key"]),
        **over,
    }
    return Settings.model_construct(**fields)


def codes(report: preflight.Report, level: str | None = None) -> set[str]:
    return {f.code for f in report.findings if level is None or f.level == level}


# ------------------------------------------------------------------- preflight


def test_good_staging_has_no_critical_and_prints_what_passed(env: Env) -> None:
    report = preflight.run(lambda: staging_db(env))
    assert codes(report, CRITICAL) == set(), report.findings
    assert exit_code(report.findings) == 0
    assert "Migrations em dia" in report.passed
    assert "RLS forçada em toda tabela com tenant_id" in report.passed
    assert "Papel do app sem superusuário e sem bypass de RLS" in report.passed


def test_warnings_for_the_optional_pieces_that_are_missing() -> None:
    report = preflight.run(
        lambda: staging(ai_api_key="", platform_cakto_secret="", capture_events=True),
        check_database=False,
    )
    assert codes(report, CRITICAL) == set()
    assert {"ai_key_missing", "platform_secrets_missing", "capture_on"} <= codes(report, WARNING)
    assert exit_code(report.findings) == 1


def test_live_whatsapp_is_flagged_so_nobody_turns_it_on_by_accident() -> None:
    report = preflight.run(lambda: staging(whatsapp_live=True), check_database=False)
    assert "whatsapp_live" in codes(report, WARNING)
    assert "whatsapp_live" not in codes(preflight.run(staging, check_database=False))


def test_dev_environment_is_only_a_warning() -> None:
    report = preflight.run(lambda: Settings(env="dev"), check_database=False)
    assert "env_dev" in codes(report, WARNING)


def test_placeholder_google_client_is_critical_in_prod_and_warning_in_staging() -> None:
    placeholder = "pendente.apps.googleusercontent.com"
    staged = preflight.run(lambda: staging(google_client_id=placeholder), check_database=False)
    assert "google_placeholder" in codes(staged, WARNING)
    prod = preflight.run(
        lambda: staging(env="prod", google_client_id=placeholder), check_database=False
    )
    assert "google_placeholder" in codes(prod, CRITICAL)


def test_same_database_user_for_api_and_migrations_is_critical() -> None:
    report = preflight.run(
        lambda: staging(database_url=BASE["database_admin_url"]), check_database=False
    )
    assert "same_db_user" in codes(report, CRITICAL)
    assert exit_code(report.findings) == 2


def test_dev_defaults_in_staging_are_refused_by_name_and_never_by_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "valor-secreto-que-nao-pode-aparecer"
    monkeypatch.setenv("FM_ENV", "staging")
    monkeypatch.setenv("FM_SECRETS_KEYS", secret)
    monkeypatch.setenv("FM_DATABASE_URL", f"postgresql://fm_app:{secret}@localhost/x")
    monkeypatch.setenv("FM_DATABASE_ADMIN_URL", f"postgresql://fm_owner:{secret}@localhost/x")
    monkeypatch.setenv("FM_COOKIE_SECURE", "false")
    report = preflight.run(lambda: Settings())
    assert codes(report) == {"settings_invalid"}
    text = " ".join(f.message for f in report.findings)
    assert secret not in text and "FM_GOOGLE_CLIENT_ID" in text and "FM_DATABASE_URL" in text


def test_invalid_encryption_key_is_critical() -> None:
    report = preflight.run(lambda: staging(secrets_keys="k1:nao-e-base64"), check_database=False)
    assert "secrets_key" in codes(report, CRITICAL)


def test_unreachable_database_is_critical_and_does_not_leak_the_password() -> None:
    bad = "postgresql://fm_owner:senha-muito-secreta@127.0.0.1:1/fm"
    report = preflight.run(lambda: staging(database_admin_url=bad))
    assert "db_admin_unreachable" in codes(report, CRITICAL)
    assert "senha-muito-secreta" not in " ".join(f.message for f in report.findings)


def test_no_db_flag_skips_every_database_check() -> None:
    report = preflight.run(staging, check_database=False)
    assert not any("Banco" in p or "Migrations" in p for p in report.passed)


def test_privileged_app_role_is_critical() -> None:
    assert preflight.role_findings({"rolsuper": False, "rolbypassrls": False}) == []
    got = dict(preflight.role_findings({"rolsuper": True, "rolbypassrls": True}))
    assert set(got) == {"app_role_superuser", "app_role_bypassrls"}
    assert preflight.role_findings(None)[0][0] == "app_role_missing"


def test_pending_migration_and_missing_rls_are_critical(env: Env) -> None:
    last = sorted(p.name for p in (ROOT / "apps/api/src/fm_seller/migrations").glob("*.sql"))[-1]
    row = env.sql("SELECT version, checksum FROM schema_migrations WHERE version = %s", (last,))[0]
    with psycopg.connect(env.admin_url, autocommit=True) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version = %s", (last,))
        conn.execute("CREATE TABLE tmp_sem_rls (tenant_id uuid)")
    try:
        report = preflight.run(lambda: staging_db(env))
        assert {"migrations_pending", "rls_not_forced"} <= codes(report, CRITICAL)
        text = " ".join(f.message for f in report.findings)
        assert last in text and "tmp_sem_rls" in text
    finally:
        with psycopg.connect(env.admin_url, autocommit=True) as conn:
            conn.execute("DROP TABLE tmp_sem_rls")
            conn.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)", tuple(row)
            )


def test_cli_preflight_prints_the_result_and_returns_the_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in {
        "FM_ENV": "staging",
        "FM_DATABASE_URL": BASE["database_url"],
        "FM_DATABASE_ADMIN_URL": BASE["database_admin_url"],
        "FM_SECRETS_KEYS": BASE["secrets_keys"],
        "FM_GOOGLE_CLIENT_ID": BASE["google_client_id"],
        "FM_COOKIE_SECURE": "true",
        "FM_TRUST_PROXY": "true",
        "FM_WEB_ORIGIN": BASE["web_origin"],
        "FM_PUBLIC_BASE_URL": BASE["public_base_url"],
        "FM_AI_API_KEY": "x",
        "FM_PLATFORM_CAKTO_SECRET": "y",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    try:
        assert cli.main(["preflight", "--no-db"]) == 0
        out = capsys.readouterr().out
        assert "RESULTADO: OK" in out and BASE["secrets_keys"] not in out
        monkeypatch.setenv("FM_AI_API_KEY", "")
        assert cli.main(["preflight", "--no-db"]) == 1
        assert "RESULTADO: COM AVISOS" in capsys.readouterr().out
        monkeypatch.setenv("FM_DATABASE_URL", BASE["database_admin_url"])
        assert cli.main(["preflight", "--no-db"]) == 2
        assert "RESULTADO: NÃO SUBA" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()


# ----------------------------------------------------------------------- smoke


SECURE = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    "referrer-policy": "no-referrer",
    "strict-transport-security": "max-age=31536000",
}


def fake_api(**over: Any) -> httpx.MockTransport:
    """API correta por padrão; cada teste estraga uma coisa."""

    def handler(req: httpx.Request) -> httpx.Response:
        path, method = req.url.path, req.method
        if path in over:
            value = over[path]
            return value(req) if callable(value) else cast(httpx.Response, value)
        if path == "/v1/health":
            return httpx.Response(200, json={"status": "ok", "version": "0.1.0"}, headers=SECURE)
        if path == "/v1/ready":
            return httpx.Response(200, json={"status": "ready"})
        if path == "/v1/me":
            return httpx.Response(401, json={"error": {"code": "unauthorized"}})
        if path == "/v1/auth/logout" and method == "POST":
            return httpx.Response(403, json={"error": {"code": "bad_origin"}})
        if path.startswith("/v1/webhooks/"):
            return httpx.Response(404, json={"error": {"code": "not_found"}})
        if path == "/v1/platform/webhooks/cakto":
            return httpx.Response(401, json={"error": {"code": "invalid_secret"}})
        if path == "/login":
            return httpx.Response(200, text="<html>AtendeVendeIA</html>", headers=SECURE)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def run_smoke(transport: httpx.MockTransport, **kw: Any) -> preflight.Report:
    sleeps: list[float] = []
    api = kw.pop("api", "https://api.example.test")
    client = httpx.Client(transport=transport)
    report = smoke.run(api, kw.pop("web", None), client=client, sleep=sleeps.append, **kw)
    report.sleeps = sleeps  # type: ignore[attr-defined]
    return report


def test_preflight_flags_missing_abuse_limits_and_proxy_trust() -> None:
    off = preflight.run(lambda: staging(rate_limit_enabled=False), check_database=False)
    assert "rate_limit_off" in codes(off, WARNING)  # em staging é aviso
    prod = preflight.run(
        lambda: staging(env="prod", rate_limit_enabled=False), check_database=False
    )
    assert "rate_limit_off" in codes(prod, CRITICAL)
    proxy = preflight.run(lambda: staging(trust_proxy=False), check_database=False)
    assert "trust_proxy_off" in codes(proxy, WARNING) and not codes(proxy, CRITICAL)


def test_smoke_good_api_passes_every_check() -> None:
    report = run_smoke(fake_api())
    assert report.findings == [] and len(report.passed) == 8, report.passed


def test_smoke_with_web_checks_login_and_the_v1_proxy() -> None:
    web = "https://app.example.test"
    assert run_smoke(fake_api(), web=web).findings == []
    assert len(run_smoke(fake_api(), web=web).passed) == 11

    good = fake_api()

    def without_proxy(req: httpx.Request) -> httpx.Response:
        if req.url.host == "app.example.test" and req.url.path == "/v1/health":
            return httpx.Response(404)  # o painel sem API_PROXY_TARGET no build
        return good.handle_request(req)

    report = run_smoke(httpx.MockTransport(without_proxy), web=web, wait=0)
    assert "web_proxy" in codes(report, CRITICAL)
    assert exit_code(report.findings) == 2


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"/v1/ready": httpx.Response(503, json={})}, "ready"),
        ({"/v1/me": httpx.Response(200, json={"user": {}})}, "me_open"),
        ({"/v1/auth/logout": httpx.Response(200, json={})}, "origin_guard"),
        ({"/v1/platform/webhooks/cakto": httpx.Response(200, json={})}, "webhook_open"),
        (
            {
                "/v1/health": lambda r: httpx.Response(
                    200, json={"status": "ok"}, headers={"access-control-allow-origin": "*"}
                )
            },
            "cors_open",
        ),
    ],
)
def test_smoke_flags_each_broken_guarantee_as_critical(override: dict[str, Any], code: str) -> None:
    report = run_smoke(fake_api(**override), wait=0)
    assert code in codes(report, CRITICAL)
    assert exit_code(report.findings) == 2


def test_smoke_warns_when_security_headers_are_missing_or_the_api_description_is_open() -> None:
    bare = httpx.Response(200, json={"status": "ok", "version": "0.1.0"})
    report = run_smoke(fake_api(**{"/v1/health": bare}), wait=0)
    assert "api_headers" in codes(report, WARNING) and exit_code(report.findings) == 1
    assert "Content-Security-Policy" in report.findings[0].message
    # HSTS só é cobrado em https fora do local
    no_hsts = {k: v for k, v in SECURE.items() if k != "strict-transport-security"}
    res = httpx.Response(200, json={"status": "ok"}, headers=no_hsts)
    assert "api_headers" in codes(run_smoke(fake_api(**{"/v1/health": res}), wait=0), WARNING)
    local = run_smoke(fake_api(**{"/v1/health": res}), api="http://localhost:8000", wait=0)
    assert "api_headers" not in codes(local, WARNING)
    exposed = fake_api(**{"/openapi.json": httpx.Response(200, json={"openapi": "3.1.0"})})
    assert "api_docs_open" in codes(run_smoke(exposed, wait=0), WARNING)
    web = "https://app.example.test"
    page = httpx.Response(200, text="<html>AtendeVendeIA</html>")
    assert "web_headers" in codes(run_smoke(fake_api(**{"/login": page}), web=web, wait=0), WARNING)


def test_smoke_stops_early_when_the_api_is_down() -> None:
    report = run_smoke(fake_api(**{"/v1/health": httpx.Response(500, json={})}), wait=0)
    assert codes(report) == {"health"} and report.passed == []


def test_smoke_waits_for_a_free_instance_to_wake_up() -> None:
    attempts = {"n": 0}

    def waking(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] <= 3:
            return httpx.Response(503, text="acordando")
        return httpx.Response(200, json={"status": "ok", "version": "0.1.0"}, headers=SECURE)

    report = run_smoke(fake_api(**{"/v1/health": waking}), wait=60)
    assert report.findings == [] and report.sleeps == [5, 5, 5]  # type: ignore[attr-defined]


def test_smoke_gives_up_after_the_wait_limit() -> None:
    report = run_smoke(fake_api(**{"/v1/health": httpx.Response(503)}), wait=10)
    assert "health" in codes(report, CRITICAL) and report.sleeps == [5, 5]  # type: ignore[attr-defined]


def test_smoke_warns_about_plain_http_outside_localhost() -> None:
    assert "api_not_https" in codes(run_smoke(fake_api(), api="http://api.example.test"), WARNING)
    assert run_smoke(fake_api(), api="http://localhost:8000").findings == []


def test_smoke_against_the_real_app_in_process(env: Env, db: Database) -> None:
    app = create_app(env.settings(), db=db, box=env.box)
    with TestClient(app, base_url="http://testserver") as tc:

        def forward(req: httpx.Request) -> httpx.Response:
            sent = {k: v for k, v in req.headers.items() if k.lower() == "origin"}
            res = tc.request(req.method, req.url.path, headers=sent)
            return httpx.Response(res.status_code, headers=res.headers, content=res.content)

        client = httpx.Client(transport=httpx.MockTransport(forward))
        report = smoke.run("http://testserver", client=client, wait=0)
    assert codes(report, CRITICAL) == set(), (report.findings, report.passed)
    assert (
        len(report.passed) == 7
    )  # inclui os cabeçalhos de segurança; /openapi.json só se confere fora do local


def test_cli_smoke_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from fm_seller.ops import smoke as smoke_mod

    ok = preflight.Report(passed=["tudo"])
    monkeypatch.setattr(smoke_mod, "run", lambda *a, **k: ok)
    assert cli.main(["smoke", "--api", "https://x.test"]) == 0
    assert "RESULTADO: OK" in capsys.readouterr().out
    bad = preflight.Report()
    bad.bad(CRITICAL, "health", "caiu")
    monkeypatch.setattr(smoke_mod, "run", lambda *a, **k: bad)
    assert cli.main(["smoke", "--api", "https://x.test"]) == 2
    assert "NÃO SUBA" in capsys.readouterr().out


# ------------------------------------------------------------------ blueprints


def blueprint(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def live_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]


def keys_of(text: str) -> set[str]:
    return set(re.findall(r"key:\s*([A-Za-z0-9_]+)", "\n".join(live_lines(text))))


FREE = ROOT / "render.yaml"
PAID = ROOT / "docs" / "render.pago.yaml"
SETTINGS_ENV = {f"FM_{name.upper()}" for name in Settings.model_fields}
API_REQUIRED = {
    "FM_ENV",
    "FM_COOKIE_SECURE",
    "FM_DATABASE_URL",
    "FM_DATABASE_ADMIN_URL",
    "FM_SECRETS_KEYS",
    "FM_GOOGLE_CLIENT_ID",
    "FM_WEB_ORIGIN",
    "FM_PUBLIC_BASE_URL",
}


@pytest.mark.parametrize("path", [FREE, PAID], ids=["render.yaml", "render.pago.yaml"])
def test_blueprint_env_vars_exist_in_settings_and_secrets_are_not_literal(path: Path) -> None:
    text = blueprint(path)
    fm_keys = {k for k in keys_of(text) if k.startswith("FM_")}
    assert fm_keys <= SETTINGS_ENV, fm_keys - SETTINGS_ENV
    assert fm_keys >= API_REQUIRED
    for line in live_lines(text):
        m = re.search(r"key:\s*([A-Z_]+)\s*,\s*value:", line)
        if m:
            assert not re.search(
                r"SECRET|TOKEN|PASSWORD|HOTTOK|API_KEY|KEYS|_URL$", m.group(1)
            ) or (m.group(1) in ("NEXT_PUBLIC_API_URL", "FM_PUBLIC_BASE_URL")), (
                f"valor literal em {m.group(1)}"
            )
    assert 'FM_COOKIE_SECURE, value: "true"' in text and 'FM_WHATSAPP_LIVE, value: "false"' in text
    assert "healthCheckPath: /v1/health" in text and "healthCheckPath: /login" in text
    for needed in ("API_PROXY_TARGET", "NEXT_PUBLIC_GOOGLE_CLIENT_ID"):
        assert needed in keys_of(text)
    assert re.search(
        r'key:\s*NEXT_PUBLIC_API_URL,\s*value:\s*""', text
    )  # vazio: painel usa o proxy


def test_blueprint_commands_are_real_cli_commands() -> None:
    cli_source = (ROOT / "apps/api/src/fm_seller/cli.py").read_text(encoding="utf-8")
    known = set(re.findall(r'add_parser\(\s*"([a-z-]+)"', cli_source))
    for path in (FREE, PAID):
        for cmd in re.findall(
            r"fm_seller\.cli\s+([a-z-]+)", "\n".join(live_lines(blueprint(path)))
        ):
            assert cmd in known, f"{path.name}: comando {cmd} não existe na CLI"


def test_free_blueprint_has_no_worker_or_cron_and_paid_has_both() -> None:
    free_live = "\n".join(live_lines(blueprint(FREE)))
    assert "type: worker" not in free_live and "type: cron" not in free_live
    assert "plan: free" in free_live and "cli bootstrap" in free_live
    paid_live = "\n".join(live_lines(blueprint(PAID)))
    assert "type: worker" in paid_live and "type: cron" in paid_live
    assert (
        "plan: free" not in paid_live
        and "preDeployCommand: python -m fm_seller.cli migrate" in paid_live
    )
    assert "fm_seller.cli worker" in paid_live and "fm_seller.cli ops-check" in paid_live


def test_worker_and_cron_in_the_paid_blueprint_carry_what_the_code_needs() -> None:
    text = "\n".join(live_lines(blueprint(PAID)))
    blocks = re.split(r"\n  - type: ", text)
    worker = next(b for b in blocks if b.startswith("worker"))
    cron = next(b for b in blocks if b.startswith("cron"))
    assert {"FM_AI_API_KEY", "FM_SECRETS_KEYS", "FM_DATABASE_URL"} <= keys_of(worker)
    assert {"FM_DATABASE_ADMIN_URL", "FM_DATABASE_URL"} <= keys_of(cron)  # ops-check lê migrations


def test_env_example_lists_every_setting_the_blueprints_use() -> None:
    example = (ROOT / "apps/api/.env.example").read_text(encoding="utf-8")
    listed = set(re.findall(r"^(FM_[A-Z_]+)=", example, flags=re.M))
    for path in (FREE, PAID):
        used = {k for k in keys_of(blueprint(path)) if k.startswith("FM_")}
        assert used <= listed, f"{path.name}: faltam no .env.example: {used - listed}"
    assert {"FM_CAPTURE_EVENTS", "FM_AI_THINKING_LEVEL"} <= listed


# --------------------------------------------------------------------- ensaio


@pytest.fixture
def pg_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for candidate in sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True):
        monkeypatch.setenv("PATH", f"{candidate}{os.pathsep}{os.environ['PATH']}")
        break
    yield


@needs_pg_tools
def test_rehearsal_script_proves_backup_upgrade_rollback_and_upgrade_again(
    env: Env, pg_path: None
) -> None:
    done = subprocess.run(  # noqa: S603 (script do repositório)
        [str(SCRIPTS / "rehearsal.sh")],
        env={**os.environ, "FM_DATABASE_ADMIN_URL": env.admin_url, "PYTHON": sys.executable},
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ENSAIO OK" in done.stdout
    for step in ("1.", "2.", "3.", "4.", "5."):
        assert f"== {step}" in done.stdout
    leftovers = env.sql("SELECT datname FROM pg_database WHERE datname LIKE 'fm_ensaio%%'")
    assert leftovers == []  # apagou os bancos temporários
    assert "Aplicadas: " in done.stdout and "OK: restauração verificada" in done.stdout


def test_rehearsal_refuses_to_run_without_a_database_url() -> None:
    done = subprocess.run(  # noqa: S603
        [str(SCRIPTS / "rehearsal.sh")],
        env={k: v for k, v in os.environ.items() if k != "FM_DATABASE_ADMIN_URL"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode != 0 and "FM_DATABASE_ADMIN_URL" in done.stderr


def test_cli_preflight_with_invalid_config_reports_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regressão: `cli preflight` quebrava com traceback se a configuração era inválida."""
    from fm_seller import cli

    secret = "valor-secreto-que-nao-pode-aparecer"
    monkeypatch.setenv("FM_ENV", "staging")
    monkeypatch.setenv("FM_SECRETS_KEYS", secret)
    monkeypatch.setenv("FM_DATABASE_URL", f"postgresql://fm_app:{secret}@localhost/x")
    monkeypatch.setenv("FM_DATABASE_ADMIN_URL", f"postgresql://fm_owner:{secret}@localhost/x")
    monkeypatch.setenv("FM_COOKIE_SECURE", "false")
    assert cli.main(["preflight", "--no-db"]) == 2
    out = capsys.readouterr().out
    assert "RESULTADO: NÃO SUBA" in out and "settings_invalid" in out
    assert secret not in out and "Traceback" not in out


def test_cli_gen_key_works_without_valid_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from fm_seller import cli

    monkeypatch.setenv("FM_ENV", "staging")  # config inválida de propósito
    monkeypatch.setenv("FM_COOKIE_SECURE", "false")
    assert cli.main(["gen-key"]) == 0
    assert capsys.readouterr().out.startswith("k1:")
