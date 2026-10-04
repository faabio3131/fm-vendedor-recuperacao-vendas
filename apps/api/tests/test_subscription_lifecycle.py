"""Ciclo de vida da assinatura: estados, carência, pausa de envios e vendedor IA, tela "Meu plano".

Eventos sintéticos (formato real da Cakto/Hotmart ainda a confirmar com evento real).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fm_seller.ai.model import SimulatedAiModel
from fm_seller.ai.seller import run_ai_replies
from fm_seller.channels.outbox import flush_outbox
from fm_seller.channels.whatsapp import upsert_conversation
from fm_seller.cli import map_product, set_grace_days
from fm_seller.db import Database
from fm_seller.events import normalize as n
from fm_seller.provisioning import lifecycle
from fm_seller.recovery.cold import detect_cold_conversations
from fm_seller.recovery.senders import SimulatedSender
from tests.conftest import Env, login, unique_email
from tests.test_platform_provisioning import SECRET, plan_of, purchase
from tests.test_platform_provisioning import pclient as pclient
from tests.test_recovery_engine import NOW, Ctx, ev
from tests.test_recovery_engine import ctx as ctx

PHONE = "5511977776666"
HOOK = "/v1/platform/webhooks/cakto"


def post(c: TestClient, email: str, event: str) -> str:
    out = c.post(HOOK, json=purchase(email, event))
    assert out.status_code == 200, out.text
    return str(out.json()["status"])


def row(env: Env, email: str) -> tuple[Any, ...]:
    return env.sql(
        "SELECT tp.status, tp.past_due_since IS NOT NULL, tp.tenant_id FROM tenant_plans tp "
        "JOIN pending_invites i ON i.tenant_id = tp.tenant_id WHERE lower(i.email) = lower(%s)",
        (email,),
    )[0]


# ------------------------------------------------------------- tabela de transições


@pytest.mark.parametrize(
    ("current", "kind", "expected"),
    [
        ("active", n.SUBSCRIPTION_LATE, "past_due"),
        ("past_due", n.SUBSCRIPTION_LATE, None),
        ("suspended", n.SUBSCRIPTION_LATE, None),
        ("canceled", n.SUBSCRIPTION_LATE, None),
        ("refunded", n.SUBSCRIPTION_LATE, None),
        ("past_due", n.SUBSCRIPTION_RECOVERED, "active"),
        ("suspended", n.SUBSCRIPTION_RECOVERED, "active"),
        ("active", n.SUBSCRIPTION_RECOVERED, None),
        ("canceled", n.SUBSCRIPTION_RECOVERED, None),
        ("refunded", n.SUBSCRIPTION_RECOVERED, None),
        ("active", n.SUBSCRIPTION_CANCELED, "canceled"),
        ("past_due", n.SUBSCRIPTION_CANCELED, "canceled"),
        ("suspended", n.SUBSCRIPTION_CANCELED, "canceled"),
        ("canceled", n.SUBSCRIPTION_CANCELED, None),
        ("refunded", n.SUBSCRIPTION_CANCELED, None),
        ("active", n.REFUNDED, "refunded"),
        ("canceled", n.REFUNDED, "refunded"),
        ("refunded", n.REFUNDED, None),
        ("canceled", n.PURCHASE_APPROVED, "active"),
        ("refunded", n.SUBSCRIPTION_ACTIVE, "active"),
        ("suspended", n.PURCHASE_APPROVED, "active"),
        ("active", n.PIX_PENDING, None),
    ],
)
def test_transition_table(current: str, kind: str, expected: str | None) -> None:
    assert lifecycle.next_status(current, kind) == expected


def test_blocking_states() -> None:
    assert [lifecycle.blocks_service(s) for s in lifecycle.STATUSES] == [
        False,
        False,
        True,
        True,
        True,
    ]
    assert lifecycle.blocks_service(None) is False  # sem plano registrado: como antes


# ------------------------------------------------------------ eventos da plataforma


def test_late_starts_the_clock_once_and_recovery_clears_it(pclient: TestClient, env: Env) -> None:
    map_product(env.admin_url, "cakto", "prod-x", "fase-1")
    email = unique_email("late")
    post(pclient, email, "purchase_approved")
    assert post(pclient, email, "subscription_late") == "plan_status_changed"
    status, has_clock, tid = row(env, email)
    assert (status, has_clock) == ("past_due", True)
    since = env.sql("SELECT past_due_since FROM tenant_plans WHERE tenant_id = %s", (tid,))[0][0]
    # outro evento de atraso (a plataforma insiste) não reinicia a contagem
    other = purchase(email, "subscription_renewal_refused")
    assert pclient.post(HOOK, json=other).json()["status"] == "plan_status_unchanged"
    assert (
        env.sql("SELECT past_due_since FROM tenant_plans WHERE tenant_id = %s", (tid,))[0][0]
        == since
    )
    assert post(pclient, email, "subscription_late_recovered") == "plan_status_changed"
    assert row(env, email)[:2] == ("active", False)


def test_out_of_order_and_repeated_events_do_not_resurrect_a_canceled_plan(
    pclient: TestClient, env: Env
) -> None:
    map_product(env.admin_url, "cakto", "prod-x", "fase-1")
    email = unique_email("ooo")
    post(pclient, email, "purchase_approved")
    assert post(pclient, email, "subscription_canceled") == "plan_status_changed"
    assert post(pclient, email, "subscription_canceled") == "duplicate"
    assert post(pclient, email, "subscription_late") == "plan_status_unchanged"
    assert post(pclient, email, "subscription_late_recovered") == "plan_status_unchanged"
    assert plan_of(env, email) == ("fase-1", "canceled")
    # só uma compra ou renovação nova reativa
    assert post(pclient, email, "subscription_renewed") == "plan_updated"
    assert plan_of(env, email) == ("fase-1", "active")


def test_refund_is_its_own_state_and_is_not_overwritten_by_cancel(
    pclient: TestClient, env: Env
) -> None:
    map_product(env.admin_url, "cakto", "prod-x", "fase-1")
    email = unique_email("rf")
    post(pclient, email, "purchase_approved")
    post(pclient, email, "refund")
    assert plan_of(env, email) == ("fase-1", "refunded")
    assert post(pclient, email, "subscription_canceled") == "plan_status_unchanged"
    assert plan_of(env, email) == ("fase-1", "refunded")


def test_every_change_is_audited_with_the_buyer_email(pclient: TestClient, env: Env) -> None:
    map_product(env.admin_url, "cakto", "prod-x", "fase-1")
    email = unique_email("aud")
    post(pclient, email, "purchase_approved")
    post(pclient, email, "subscription_late")
    post(pclient, email, "subscription_canceled")
    tid = row(env, email)[2]
    actions = [
        a[0]
        for a in env.sql(
            "SELECT action FROM audit_log WHERE tenant_id = %s AND action LIKE 'plan.%%' "
            "ORDER BY id",
            (tid,),
        )
    ]
    assert actions[-2:] == ["plan.past_due", "plan.canceled"]
    assert env.sql(
        "SELECT target FROM audit_log WHERE tenant_id = %s AND action = 'plan.past_due'", (tid,)
    ) == [(email,)]


# ------------------------------------------------------------------------ carência


def _past_due_tenant(env: Env, days_ago: float, plan: str = "fase-1") -> str:
    tid = env.tenant("Loja Atraso", unique_email("grace"), plan)
    env.sql(
        "UPDATE tenant_plans SET status = 'past_due', past_due_since = now() - %s * interval "
        "'1 day' WHERE tenant_id = %s",
        (days_ago, tid),
    )
    return tid


def test_grace_ends_and_the_tenant_is_suspended_only_after_it(env: Env, db: Database) -> None:
    inside = _past_due_tenant(env, 1)
    outside = _past_due_tenant(env, 4)  # carência padrão: 3 dias
    done = lifecycle.enforce_grace(db)
    assert done >= 1
    status = {
        t: env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (t,))[0][0]
        for t in (inside, outside)
    }
    assert status == {inside: "past_due", outside: "suspended"}
    assert lifecycle.enforce_grace(db) == 0  # repetir não muda nada
    assert env.sql(
        "SELECT target FROM audit_log WHERE tenant_id = %s AND action = 'plan.suspended'",
        (outside,),
    ) == [("carencia_esgotada",)]
    assert env.sql("SELECT past_due_since FROM tenant_plans WHERE tenant_id = %s", (outside,)) == [
        (None,)
    ]


def test_grace_days_is_data_per_plan(env: Env, db: Database) -> None:
    assert set_grace_days(env.admin_url, "fase-2", 10) is True
    try:
        tid = _past_due_tenant(env, 5, plan="fase-2")
        lifecycle.enforce_grace(db)
        assert env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (tid,)) == [
            ("past_due",)
        ]
        set_grace_days(env.admin_url, "fase-2", 0)
        lifecycle.enforce_grace(db)
        assert env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (tid,)) == [
            ("suspended",)
        ]
    finally:
        set_grace_days(env.admin_url, "fase-2", 3)
    assert set_grace_days(env.admin_url, "nao-existe", 3) is False


def test_recovered_payment_after_suspension_reactivates_with_data_intact(
    pclient: TestClient, env: Env, db: Database
) -> None:
    map_product(env.admin_url, "cakto", "prod-x", "fase-1")
    email = unique_email("back")
    post(pclient, email, "purchase_approved")
    post(pclient, email, "subscription_late")
    tid = row(env, email)[2]
    env.sql(
        "UPDATE tenant_plans SET past_due_since = now() - interval '5 days' WHERE tenant_id = %s",
        (tid,),
    )
    lifecycle.enforce_grace(db)
    assert plan_of(env, email) == ("fase-1", "suspended")
    env.sql(
        "INSERT INTO offers (tenant_id, name, price_cents, payment_url) "
        "VALUES (%s, 'Oferta guardada', 100, 'https://pay.example.test/o')",
        (tid,),
    )
    assert post(pclient, email, "subscription_late_recovered") == "plan_status_changed"
    assert plan_of(env, email) == ("fase-1", "active")
    assert env.sql("SELECT count(*) FROM offers WHERE tenant_id = %s", (tid,)) == [(1,)]


def test_manual_suspend_and_reactivate_keep_everything(env: Env, db: Database) -> None:
    tid = env.tenant("Loja Manual", unique_email("man"))
    t = uuid.UUID(tid)
    assert lifecycle.set_plan_status(db, t, "suspended") is True
    assert env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (tid,)) == [
        ("suspended",)
    ]
    assert lifecycle.set_plan_status(db, t, "active") is True
    assert env.sql("SELECT status FROM tenant_plans WHERE tenant_id = %s", (tid,)) == [("active",)]
    assert env.sql("SELECT count(*) FROM tenants WHERE id = %s", (tid,)) == [(1,)]
    assert lifecycle.set_plan_status(db, uuid.uuid4(), "suspended") is False


# --------------------------------------------------- pausa de envios e vendedor IA


class Blocked:
    """Cliente com uma conversa de WhatsApp aberta, para medir o que cada estado deixa fazer."""

    def __init__(self, env: Env, db: Database) -> None:
        env.sql(
            "UPDATE messages SET status = 'failed' WHERE direction = 'out' AND status = 'queued'"
        )
        env.sql("UPDATE messages SET handled = true WHERE direction = 'in'")
        self.env, self.db = env, db
        self.tid = env.tenant("Loja Pausa", unique_email("pause"))
        self.t = uuid.UUID(self.tid)
        env.sql(
            "INSERT INTO tenant_settings (tenant_id, ai_enabled) VALUES (%s, true)", (self.tid,)
        )
        env.sql(
            "INSERT INTO offers (tenant_id, name, description, price_cents, payment_url) "
            "VALUES (%s, 'Curso X', 'Aprenda X', 19700, 'https://pay.example.test/x')",
            (self.tid,),
        )
        env.sql(
            "INSERT INTO connections (tenant_id, provider, public_id, config_encrypted, status) "
            "VALUES (%s, 'whatsapp_cloud', %s, %s, 'connected')",
            (
                self.tid,
                uuid.uuid4().hex[:16],
                env.box.encrypt(
                    {"phone_number_id": "1", "access_token": "EAAGtoken123456"},
                    tenant_id=self.tid,
                    provider="whatsapp_cloud",
                ),
            ),
        )
        with db.tx(tenant_id=self.t) as conn:
            _, self.conv = upsert_conversation(conn, self.t, PHONE, "Bia")
        env.sql("UPDATE conversations SET last_inbound_at = now() WHERE id = %s", (self.conv,))

    def plan(self, status: str) -> None:
        self.env.sql("UPDATE tenant_plans SET status = %s WHERE tenant_id = %s", (status, self.tid))

    def queue(self, body: str, author: str = "human") -> None:
        self.env.sql(
            "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status) "
            "VALUES (%s, %s, 'out', %s, %s, 'queued')",
            (self.tid, self.conv, author, body),
        )

    def sent(self, body: str) -> list[tuple[Any, ...]]:
        return self.env.sql(
            "SELECT status, error FROM messages WHERE tenant_id = %s AND body = %s",
            (self.tid, body),
        )


@pytest.fixture
def blocked(env: Env, db: Database) -> Blocked:
    return Blocked(env, db)


@pytest.mark.parametrize("status", ["suspended", "canceled", "refunded"])
def test_outbox_pauses_sends_but_still_confirms_an_opt_out(
    blocked: Blocked, env: Env, db: Database, status: str
) -> None:
    blocked.plan(status)
    blocked.queue(f"[{status}] mensagem da loja")
    blocked.queue(f"[{status}] confirmação de SAIR", author="system")
    sim = SimulatedSender()
    flush_outbox(db, env.box, sim)
    assert blocked.sent(f"[{status}] mensagem da loja") == [("failed", "assinatura_inativa")]
    assert blocked.sent(f"[{status}] confirmação de SAIR") == [("sent", None)]
    assert [t for _, t in sim.texts] == [f"[{status}] confirmação de SAIR"]


@pytest.mark.parametrize("status", ["active", "past_due"])
def test_outbox_sends_while_active_or_inside_the_grace(
    blocked: Blocked, env: Env, db: Database, status: str
) -> None:
    blocked.plan(status)
    blocked.queue(f"[{status}] segue normal")
    sim = SimulatedSender()
    flush_outbox(db, env.box, sim)
    assert blocked.sent(f"[{status}] segue normal") == [("sent", None)]


def test_reactivation_resumes_sending(blocked: Blocked, env: Env, db: Database) -> None:
    blocked.plan("suspended")
    blocked.queue("[volta] antes")
    flush_outbox(db, env.box, SimulatedSender())
    assert blocked.sent("[volta] antes") == [("failed", "assinatura_inativa")]
    blocked.plan("active")
    blocked.queue("[volta] depois")
    flush_outbox(db, env.box, SimulatedSender())
    assert blocked.sent("[volta] depois") == [("sent", None)]


def test_seller_ai_does_not_answer_when_suspended_and_hands_off_with_a_reason(
    blocked: Blocked, env: Env, db: Database
) -> None:
    blocked.plan("suspended")
    env.sql(
        "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status, "
        "handled) VALUES (%s, %s, 'in', 'customer', 'quanto custa?', 'received', false)",
        (blocked.tid, blocked.conv),
    )

    model = SimulatedAiModel()
    stats = run_ai_replies(db, model)
    assert stats.handoffs == 1 and model.calls == []
    assert env.sql(
        "SELECT status, handoff_reason FROM conversations WHERE id = %s", (blocked.conv,)
    ) == [("human", "assinatura_inativa")]
    assert env.sql(
        "SELECT count(*) FROM messages WHERE conversation_id = %s AND direction = 'out'",
        (blocked.conv,),
    ) == [(0,)]


def test_seller_ai_still_answers_inside_the_grace(blocked: Blocked, env: Env, db: Database) -> None:
    blocked.plan("past_due")
    env.sql(
        "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status, "
        "handled) VALUES (%s, %s, 'in', 'customer', 'quanto custa?', 'received', false)",
        (blocked.tid, blocked.conv),
    )
    assert run_ai_replies(db, SimulatedAiModel()).replied == 1


def test_recovery_steps_are_skipped_while_blocked(ctx: Ctx) -> None:
    ctx.handle(ev())
    ctx.env.sql(
        "UPDATE tenant_plans SET status = 'suspended' WHERE tenant_id = %s", (str(ctx.tid),)
    )
    ctx.run(NOW + timedelta(hours=1))
    first = ctx.steps()[0]
    assert (first["status"], first["detail"]) == ("skipped", "assinatura_inativa")
    assert ctx.sender.sent == []


def test_cold_conversations_are_not_detected_while_blocked(
    blocked: Blocked, env: Env, db: Database
) -> None:
    env.sql(
        "INSERT INTO tenant_settings (tenant_id, recovery_enabled, cold_enabled, "
        "consent_declared_at) VALUES (%s, true, true, now()) ON CONFLICT (tenant_id) DO UPDATE "
        "SET recovery_enabled = true, cold_enabled = true, consent_declared_at = now()",
        (blocked.tid,),
    )
    blocked.queue("[fria] resposta", author="human")
    flush_outbox(db, env.box, SimulatedSender())
    env.sql(
        "UPDATE messages SET created_at = now() - interval '30 hours' WHERE tenant_id = %s",
        (blocked.tid,),
    )
    blocked.plan("suspended")
    assert detect_cold_conversations(db) == 0
    blocked.plan("active")
    assert detect_cold_conversations(db) >= 1


# -------------------------------------------------------------------- Meu plano


def test_my_plan_states_and_suspended_buyer_can_still_log_in(
    pclient: TestClient, env: Env, db: Database
) -> None:
    map_product(env.admin_url, "cakto", "prod-x", "fase-1")
    email = unique_email("mp")
    post(pclient, email, "purchase_approved")
    assert login(pclient, email).status_code == 200
    ok = pclient.get("/v1/plan").json()
    assert ok["state"] == "active" and ok["status"] == "active"
    assert ok["plan"]["key"] == "fase-1" and ok["grace_ends_at"] is None
    assert set(ok["ai"]) >= {"replies", "limit", "percent"} and ok["number_daily_limit"] >= 1

    post(pclient, email, "subscription_late")
    grace = pclient.get("/v1/plan").json()
    assert grace["state"] == "grace" and grace["status"] == "past_due"
    assert grace["grace_days"] == 3 and grace["grace_days_left"] in (2, 3)
    assert pclient.get("/v1/me").json()["features"] != []  # em carência: tudo liberado

    tid = row(env, email)[2]
    env.sql(
        "UPDATE tenant_plans SET past_due_since = now() - interval '5 days' WHERE tenant_id = %s",
        (tid,),
    )
    lifecycle.enforce_grace(db)
    blocked = pclient.get("/v1/plan")  # a conta continua entrando para ver o motivo
    assert blocked.status_code == 200
    body = blocked.json()
    assert body["state"] == "blocked" and body["status"] == "suspended"
    me = pclient.get("/v1/me").json()
    assert me["plan"]["status"] == "suspended" and me["features"] == []
    assert all(not p["enabled"] for p in pclient.get("/v1/providers").json())


def test_my_plan_shows_only_the_own_tenant(client: TestClient, env: Env) -> None:
    a_email, b_email = unique_email("pa"), unique_email("pb")
    env.tenant("Loja A", a_email, "fase-1")
    b = env.tenant("Loja B", b_email, "fase-2")
    env.sql("UPDATE tenant_plans SET status = 'refunded' WHERE tenant_id = %s", (b,))
    assert login(client, a_email).status_code == 200
    a_view = client.get("/v1/plan").json()
    assert a_view["plan"]["key"] == "fase-1" and a_view["status"] == "active"
    assert login(client, b_email).status_code == 200
    b_view = client.get("/v1/plan").json()
    assert b_view["status"] == "refunded" and b_view["state"] == "blocked"


def test_my_plan_requires_login(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/v1/plan").status_code in (401, 403)


def test_secret_and_constants_are_not_leaked_by_the_plan_view(client: TestClient, env: Env) -> None:
    email = unique_email("leak")
    env.tenant("Loja Leak", email)
    assert login(client, email).status_code == 200
    text = client.get("/v1/plan").text
    assert SECRET not in text and "config_encrypted" not in text
    assert datetime.now(UTC).year >= 2026
