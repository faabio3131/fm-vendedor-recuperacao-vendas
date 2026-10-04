"""Verificação operacional: batimento do worker e achados de alerta, em banco só deste arquivo."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from fm_seller import cli
from fm_seller.db import Database
from fm_seller.events import normalize as n
from fm_seller.events.normalize import CheckoutEvent
from fm_seller.migrate import apply_migrations
from fm_seller.ops import health
from fm_seller.recovery.defaults import DEFAULT_TEMPLATES
from fm_seller.recovery.engine import handle_event
from fm_seller.recovery.senders import SimulatedSender, UnavailableSender
from tests.conftest import OWNER, Env, unique_email

NOW = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)


def drop_db(name: str) -> None:
    """Remove o banco; o pool do app pode demorar a fechar a última conexão, então tenta de novo."""
    for attempt in range(20):
        try:
            with psycopg.connect(f"{OWNER}/postgres", autocommit=True) as conn:
                conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
            return
        except psycopg.errors.InsufficientPrivilege:
            if attempt == 19:
                raise
            time.sleep(0.25)


@pytest.fixture
def ops(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Env, Database]]:
    """Banco novo por teste: os achados contam o banco todo, sem resto de outros testes."""
    dbname = f"fm_ops_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(f"{OWNER}/postgres", autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    env = Env(dbname)
    apply_migrations(env.admin_url)
    database = Database(env.app_url)
    database.open()
    monkeypatch.setattr(cli, "get_settings", lambda: env.settings())
    yield env, database
    database.close()
    drop_db(dbname)


def codes(findings: list[health.Finding]) -> set[str]:
    return {f.code for f in findings}


def beat(env: Env, at: datetime, error: str | None = None) -> None:
    env.sql("DELETE FROM worker_heartbeat")
    env.sql(
        "INSERT INTO worker_heartbeat (worker, last_cycle_at, cycles, last_error) "
        "VALUES ('recovery', %s, 1, %s)",
        (at, error),
    )


def open_case_with_steps(env: Env, database: Database) -> uuid.UUID:
    tid = uuid.UUID(env.tenant("Loja Ops", unique_email("ops")))
    env.sql(
        "INSERT INTO tenant_settings (tenant_id, recovery_enabled, consent_declared_at) "
        "VALUES (%s, true, now())",
        (tid,),
    )
    for key, body in DEFAULT_TEMPLATES.items():
        env.sql(
            "INSERT INTO message_templates (tenant_id, key, body, meta_status) "
            "VALUES (%s, %s, %s, 'approved')",
            (tid, key, body),
        )
    event = CheckoutEvent(
        kind=n.ABANDONED_CART,
        source_event="x",
        external_ref="r1",
        name="ana",
        email="ana@example.test",
        phone="5511999998888",
        product_id="p1",
        product_name="Curso",
        amount_cents=9700,
        payment_url="https://pay.example.test/c",
    )
    with database.tx(tenant_id=tid) as conn:
        handle_event(conn, tid, event, now=NOW)
    return tid


def test_no_heartbeat_is_critical_unless_not_required(ops: tuple[Env, Database]) -> None:
    _, db = ops
    assert codes(health.check(db, SimulatedSender(), now=NOW)) == {"worker_nunca_rodou"}
    assert health.check(db, SimulatedSender(), now=NOW, require_worker=False) == []


def test_fresh_heartbeat_is_ok_and_stale_is_critical(ops: tuple[Env, Database]) -> None:
    env, db = ops
    beat(env, NOW - timedelta(seconds=40))
    assert health.check(db, SimulatedSender(), now=NOW) == []
    beat(env, NOW - timedelta(minutes=6))
    found = health.check(db, SimulatedSender(), now=NOW)
    assert codes(found) == {"worker_parado"}
    assert health.exit_code(found) == 2


def test_worker_error_is_a_warning(ops: tuple[Env, Database]) -> None:
    env, db = ops
    beat(env, NOW - timedelta(seconds=10), error="OperationalError")
    found = health.check(db, SimulatedSender(), now=NOW)
    assert codes(found) == {"worker_com_erro"}
    assert health.exit_code(found) == 1


def test_overdue_steps_alert_only_when_sending_is_available(ops: tuple[Env, Database]) -> None:
    env, db = ops
    open_case_with_steps(env, db)
    beat(env, NOW)
    # Passo 1 vence 30 min depois de NOW; 20 min depois disso já está atrasado.
    later = NOW + timedelta(minutes=50)
    beat(env, later)
    assert "passos_atrasados" in codes(health.check(db, SimulatedSender(), now=later))
    # Envio desligado (staging sem FM_WHATSAPP_LIVE): acúmulo esperado, sem alerta.
    assert health.check(db, UnavailableSender(), now=later) == []
    # Ainda dentro da tolerância: sem alerta.
    soon = NOW + timedelta(minutes=40)
    beat(env, soon)
    assert health.check(db, SimulatedSender(), now=soon) == []


def test_stuck_outbox_and_send_failures(ops: tuple[Env, Database]) -> None:
    env, db = ops
    tid = open_case_with_steps(env, db)
    beat(env, NOW + timedelta(days=1))
    conv = env.sql(
        "INSERT INTO conversations (tenant_id, contact_id) "
        "SELECT tenant_id, id FROM contacts WHERE tenant_id = %s LIMIT 1 RETURNING id",
        (tid,),
    )[0][0]
    now = NOW + timedelta(hours=1)
    beat(env, now)
    env.sql("DELETE FROM recovery_steps")
    env.sql(
        "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status, "
        "created_at) "
        "VALUES (%s, %s, 'out', 'bot', 'oi', 'queued', %s)",
        (tid, conv, now - timedelta(minutes=11)),
    )
    assert codes(health.check(db, SimulatedSender(), now=now)) == {"fila_saida_parada"}
    env.sql("DELETE FROM messages")
    # 5 falhas em 5 tentativas nas últimas 24 h: alerta. Com muitos envios bons, o ruído passa.
    for _ in range(5):
        env.sql(
            "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status, "
            "created_at) "
            "VALUES (%s, %s, 'out', 'bot', 'oi', 'failed', %s)",
            (tid, conv, now - timedelta(hours=1)),
        )
    assert codes(health.check(db, SimulatedSender(), now=now)) == {"falhas_de_envio"}
    for _ in range(40):
        env.sql(
            "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status, "
            "created_at) "
            "VALUES (%s, %s, 'out', 'bot', 'oi', 'sent', %s)",
            (tid, conv, now - timedelta(hours=1)),
        )
    assert health.check(db, SimulatedSender(), now=now) == []


def test_failed_events_and_templates_waiting(ops: tuple[Env, Database]) -> None:
    env, db = ops
    tid = open_case_with_steps(env, db)
    now = NOW + timedelta(hours=2)
    beat(env, now)
    env.sql(
        "INSERT INTO platform_events (provider, dedupe_key, event_type, payload_encrypted, "
        "outcome, received_at) VALUES ('cakto', 'k1', 'purchase', '\\x00', 'failed', %s)",
        (now - timedelta(minutes=20),),
    )
    env.sql(
        "UPDATE message_templates SET meta_status = 'submitted', meta_name = 'pix_1', "
        "meta_synced_at = %s WHERE tenant_id = %s AND key = 'pix_1'",
        (now - timedelta(minutes=45), tid),
    )
    found = health.check(db, UnavailableSender(), now=now)
    assert codes(found) == {"eventos_falhos", "sync_templates_parado"}
    assert health.exit_code(found) == 1
    assert health.exit_code([]) == 0


def test_pending_migrations_detects_missing_version(ops: tuple[Env, Database]) -> None:
    env, _ = ops
    assert health.pending_migrations(env.admin_url) == []
    env.sql("DELETE FROM schema_migrations WHERE version = '0007_operacao.sql'")
    assert health.pending_migrations(env.admin_url) == ["0007_operacao.sql"]


def test_worker_records_heartbeat_and_keeps_going_after_a_failed_cycle(
    ops: tuple[Env, Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    env, _ = ops
    cli.run_worker(30, once=True)
    rows = env.sql(
        "SELECT cycles, last_error, last_cycle_at > now() - interval '1 minute' "
        "FROM worker_heartbeat WHERE worker = 'recovery'"
    )
    assert rows[0][0] == 1 and rows[0][1] is None and rows[0][2] is True

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("segredo-que-nao-deve-ir-para-o-banco")

    # Ciclo com erro: grava só o tipo do erro e não mexe no horário do último ciclo bom.
    before = env.sql("SELECT last_cycle_at FROM worker_heartbeat")[0][0]
    monkeypatch.setattr(cli, "_cycle", boom)
    with pytest.raises(RuntimeError):
        cli.run_worker(30, once=True)
    row = env.sql("SELECT last_error, last_cycle_at, cycles FROM worker_heartbeat")[0]
    assert row[0] == "RuntimeError"
    assert "segredo" not in row[0]
    assert row[1] == before and row[2] == 1


def test_ops_check_command_exit_codes(
    ops: tuple[Env, Database], capsys: pytest.CaptureFixture[str]
) -> None:
    env, _ = ops
    settings = env.settings()
    assert cli.ops_check_command(settings, require_worker=True) == 2
    assert "worker_nunca_rodou" in capsys.readouterr().out
    cli.run_worker(30, once=True)
    assert cli.ops_check_command(settings, require_worker=True) == 0
    assert "OK" in capsys.readouterr().out
    env.sql("DELETE FROM schema_migrations WHERE version = '0007_operacao.sql'")
    assert cli.ops_check_command(settings, require_worker=True) == 2
    assert "migrations_pendentes" in capsys.readouterr().out


SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "ops"
# No CI as ferramentas são obrigatórias (FM_REQUIRE_PG_TOOLS): lá o teste nunca pode ser pulado.
needs_pg_tools = pytest.mark.skipif(
    (shutil.which("pg_dump") is None or shutil.which("psql") is None)
    and not os.environ.get("FM_REQUIRE_PG_TOOLS"),
    reason="pg_dump/psql não instalados",
)


def run_script(name: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 (script do repositório, argumentos de teste)
        [str(SCRIPTS / name), *args],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def scratch_db() -> Iterator[str]:
    """Banco vazio para a restauração; devolve a URL do dono."""
    name = f"fm_restore_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(f"{OWNER}/postgres", autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    yield f"{OWNER}/{name}"
    drop_db(name)


@needs_pg_tools
def test_backup_and_restore_roundtrip_keeps_data_and_rls(
    ops: tuple[Env, Database], scratch_db: str, tmp_path: Path
) -> None:
    env, db = ops
    open_case_with_steps(env, db)
    done = run_script("backup.sh", str(tmp_path), FM_DATABASE_ADMIN_URL=env.admin_url)
    assert done.returncode == 0, done.stderr
    dumps = list(tmp_path.glob("fm_seller_*.dump"))
    assert len(dumps) == 1
    assert oct(dumps[0].stat().st_mode & 0o777) == "0o600"

    res = run_script(
        "restore_check.sh",
        str(dumps[0]),
        RESTORE_DATABASE_URL=scratch_db,
        SOURCE_DATABASE_URL=env.admin_url,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "OK: restauração verificada." in res.stdout
    assert "recovery_cases=1" in res.stdout and "tenants=1" in res.stdout
    assert "RLS ativada e forçada" in res.stdout


@needs_pg_tools
def test_backup_refuses_a_table_the_dump_could_not_read(
    ops: tuple[Env, Database], tmp_path: Path
) -> None:
    env, _ = ops
    # Tabela nova com RLS e sem política de sistema: o pg_dump a deixaria vazia, sem erro.
    env.sql("CREATE TABLE nova (tenant_id uuid)")
    env.sql("ALTER TABLE nova ENABLE ROW LEVEL SECURITY")
    done = run_script("backup.sh", str(tmp_path), FM_DATABASE_ADMIN_URL=env.admin_url)
    assert done.returncode == 1
    assert "nova" in done.stderr
    assert list(tmp_path.glob("*.dump")) == []


@needs_pg_tools
def test_restore_check_refuses_nonempty_target_and_same_database(
    ops: tuple[Env, Database], tmp_path: Path
) -> None:
    env, _ = ops
    dump = tmp_path / "x.dump"
    dump.write_bytes(b"")
    res = run_script("restore_check.sh", str(dump), RESTORE_DATABASE_URL=env.admin_url)
    assert res.returncode == 2 and "não está vazio" in res.stderr
    res = run_script(
        "restore_check.sh",
        str(dump),
        RESTORE_DATABASE_URL=env.admin_url,
        SOURCE_DATABASE_URL=env.admin_url,
    )
    assert res.returncode == 2 and "mesmo banco" in res.stderr


@needs_pg_tools
def test_rls_invariant_is_empty_on_the_real_schema_and_catches_a_gap(
    ops: tuple[Env, Database],
) -> None:
    env, _ = ops
    sql = (SCRIPTS / "rls_invariant.sql").read_text(encoding="utf-8")
    assert env.sql(sql) == []
    env.sql("CREATE TABLE sem_rls (tenant_id uuid)")
    assert env.sql(sql) == [("sem_rls",)]
