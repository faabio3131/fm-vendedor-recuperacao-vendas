"""Verificações operacionais para alerta: o que está parado ou falhando agora.

Só lê o banco (papel do app, modo sistema). Cada achado tem nível `critical` (acordar alguém) ou
`warning` (olhar no dia seguinte). Os limites são constantes aqui, em um lugar só.

Não cobre: webhook que deixou de chegar (não há como distinguir "sem vendas" de "webhook quebrado"
só pelo banco) nem a API fora do ar. Para isso use verificação externa em `/v1/ready`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import resources

import psycopg

from fm_seller.config import Settings
from fm_seller.db import Conn, Database
from fm_seller.recovery.senders import MessageSender

WORKER_STALE = timedelta(minutes=5)  # o ciclo normal é de 30 s
STEP_LATE = timedelta(minutes=15)  # passo devido que ninguém pegou
OUTBOX_LATE = timedelta(minutes=10)  # mensagem na fila de saída sem sair
EVENT_FAILED_AFTER = timedelta(minutes=15)  # o worker já tentou de novo várias vezes
TEMPLATE_SYNC_LATE = timedelta(
    minutes=30
)  # a sincronização roda a cada 5 min (30 se nada pendente)
SEND_WINDOW = timedelta(hours=24)
SEND_FAILURE_MIN = 5  # falhas mínimas na janela para alertar
SEND_FAILURE_RATIO = 0.2  # e pelo menos 20% das tentativas

CRITICAL = "critical"
WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    message: str


def _one(conn: Conn, sql: str, params: tuple[object, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return int(next(iter(row.values())))


def check(
    db: Database,
    sender: MessageSender,
    *,
    now: datetime | None = None,
    require_worker: bool = True,
    settings: Settings | None = None,
) -> list[Finding]:
    """Devolve os achados atuais. Lista vazia = tudo em ordem."""
    now = now or datetime.now(UTC)
    found: list[Finding] = []
    with db.tx(system=True) as conn:
        hb = conn.execute(
            "SELECT last_cycle_at, last_error FROM worker_heartbeat WHERE worker = 'recovery'"
        ).fetchone()
        if hb is None:
            if require_worker:
                found.append(
                    Finding(
                        CRITICAL, "worker_nunca_rodou", "O worker ainda não registrou nenhum ciclo."
                    )
                )
        else:
            age = now - hb["last_cycle_at"]
            if age > WORKER_STALE:
                mins = int(age.total_seconds() // 60)
                found.append(
                    Finding(
                        CRITICAL, "worker_parado", f"O worker não conclui um ciclo há {mins} min."
                    )
                )
            if hb["last_error"]:
                found.append(
                    Finding(
                        WARNING, "worker_com_erro", f"Último erro do worker: {hb['last_error']}"
                    )
                )

        # Com envio desligado (staging sem FM_WHATSAPP_LIVE) o acúmulo é esperado, não é falha.
        if sender.available:
            late = _one(
                conn,
                "SELECT count(*) FROM recovery_steps "
                "WHERE status = 'scheduled' AND scheduled_at < %s",
                (now - STEP_LATE,),
            )
            if late:
                found.append(
                    Finding(
                        CRITICAL,
                        "passos_atrasados",
                        f"{late} passo(s) de recuperação devidos há mais de 15 min.",
                    )
                )
            stuck = _one(
                conn,
                "SELECT count(*) FROM messages "
                "WHERE direction = 'out' AND status = 'queued' AND created_at < %s",
                (now - OUTBOX_LATE,),
            )
            if stuck:
                found.append(
                    Finding(
                        CRITICAL,
                        "fila_saida_parada",
                        f"{stuck} mensagem(ns) na fila de saída há mais de 10 min.",
                    )
                )
            since = now - SEND_WINDOW
            sent = _one(
                conn,
                "SELECT (SELECT count(*) FROM recovery_steps "
                "WHERE status = 'sent' AND sent_at >= %s) "
                "+ (SELECT count(*) FROM messages WHERE direction = 'out' "
                "AND status IN ('sent','delivered','read') AND created_at >= %s)",
                (since, since),
            )
            failed = _one(
                conn,
                "SELECT (SELECT count(*) FROM recovery_steps "
                "WHERE status = 'failed' AND claimed_at >= %s) "
                "+ (SELECT count(*) FROM messages WHERE direction = 'out' "
                "AND status = 'failed' AND created_at >= %s)",
                (since, since),
            )
            if failed >= SEND_FAILURE_MIN and failed >= SEND_FAILURE_RATIO * (sent + failed):
                found.append(
                    Finding(
                        WARNING,
                        "falhas_de_envio",
                        f"{failed} envio(s) falharam nas últimas 24 h "
                        f"(de {sent + failed} tentativas).",
                    )
                )

        bad_events = _one(
            conn,
            "SELECT (SELECT count(*) FROM webhook_events WHERE status = 'failed' "
            "AND received_at < %s) + (SELECT count(*) FROM platform_events "
            "WHERE outcome = 'failed' AND received_at < %s)",
            (now - EVENT_FAILED_AFTER, now - EVENT_FAILED_AFTER),
        )
        if bad_events:
            found.append(
                Finding(
                    WARNING,
                    "eventos_falhos",
                    f"{bad_events} evento(s) de entrada seguem falhando após novas tentativas.",
                )
            )

        waiting = _one(
            conn,
            "SELECT count(*) FROM message_templates WHERE meta_status = 'submitted' "
            "AND coalesce(meta_synced_at, 'epoch'::timestamptz) < %s",
            (now - TEMPLATE_SYNC_LATE,),
        )
        if waiting:
            found.append(
                Finding(
                    WARNING,
                    "sync_templates_parado",
                    f"{waiting} template(s) aguardando a Meta sem sincronizar há mais de 30 min.",
                )
            )
    if settings is not None and settings.fmcommand_mode != "off":
        found.extend(license_findings(db, settings, now))
    return found


def license_findings(db: Database, settings: Settings, now: datetime) -> list[Finding]:
    """Achados do Billing Central. Só existem fora do modo `off`; nada aqui lê dado de pessoa."""
    found: list[Finding] = []
    with db.tx(system=True) as conn:
        sync = conn.execute(
            "SELECT last_success_at, last_error, consecutive_failures FROM license_sync_state"
        ).fetchone()
        if settings.fmcommand_api_base_url and sync is not None:
            stale = timedelta(minutes=settings.fmcommand_stale_after_minutes)
            last = sync["last_success_at"]
            if last is None or now - last > stale:
                found.append(
                    Finding(
                        WARNING,
                        "licencas_sem_reconciliacao",
                        "A reconciliação de licenças com o Command não teve sucesso há mais de "
                        f"{settings.fmcommand_stale_after_minutes} min"
                        + (f" (último erro: {sync['last_error']})." if sync["last_error"] else "."),
                    )
                )
        active = _one(
            conn,
            "SELECT count(*) FROM commercial_links WHERE authority = 'fmcommand' "
            "AND contingency_started_at IS NOT NULL AND contingency_exhausted_at IS NULL",
        )
        if active:
            found.append(
                Finding(
                    WARNING,
                    "licencas_em_contingencia",
                    f"{active} licença(s) em contingência (máximo 72 h, sem renovação automática).",
                )
            )
        exhausted = _one(
            conn,
            "SELECT count(*) FROM commercial_links WHERE authority = 'fmcommand' "
            "AND contingency_exhausted_at IS NOT NULL AND state IN ('active', 'past_due')",
        )
        if exhausted:
            found.append(
                Finding(
                    CRITICAL,
                    "licencas_contingencia_esgotada",
                    f"{exhausted} cliente(s) suspensos por contingência esgotada: confirmar com o "
                    "Command.",
                )
            )
        failed = _one(
            conn,
            "SELECT count(*) FROM license_events WHERE outcome = 'failed' AND received_at < %s",
            (now - EVENT_FAILED_AFTER,),
        )
        if failed:
            found.append(
                Finding(
                    WARNING,
                    "eventos_de_licenca_falhos",
                    f"{failed} evento(s) de licença falharam (a reconciliação tenta corrigir).",
                )
            )
    return found


def pending_migrations(admin_url: str) -> list[str]:
    """Migrations do código que ainda não constam no banco (precisa do papel dono)."""
    root = resources.files("fm_seller") / "migrations"
    expected = sorted(p.name for p in root.iterdir() if p.name.endswith(".sql"))
    with psycopg.connect(admin_url) as conn:
        exists = conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone()
        if exists is None or exists[0] is None:
            return expected
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    return [name for name in expected if name not in done]


def exit_code(findings: list[Finding]) -> int:
    """0 = em ordem, 1 = só avisos, 2 = há algo crítico."""
    if any(f.level == CRITICAL for f in findings):
        return 2
    return 1 if findings else 0
