"""Motor de recuperação: abertura de caso, agendamento, envio (simulado) e regras de segurança."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from fm_seller.db import Database
from fm_seller.events import normalize as n
from fm_seller.events.normalize import CheckoutEvent
from fm_seller.recovery.defaults import DEFAULT_TEMPLATES
from fm_seller.recovery.engine import handle_event, reap_stuck, run_due_steps
from fm_seller.recovery.senders import SimulatedSender, UnavailableSender
from tests.conftest import Env, login, unique_email

NOW = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)  # segunda, 11h em São Paulo
PHONE = "5511999998888"


def ev(
    kind: str = n.ABANDONED_CART,
    ref: str = "r1",
    product: str = "Curso",
    phone: str | None = PHONE,
    email: str | None = "ana@example.test",
    cents: int = 9700,
) -> CheckoutEvent:
    return CheckoutEvent(
        kind=kind,
        source_event="x",
        external_ref=ref,
        name="ana souza",
        email=email,
        phone=phone,
        product_id="p1",
        product_name=product,
        amount_cents=cents,
        payment_url="https://pay.example.test/c",
    )


class Ctx:
    def __init__(self, env: Env, db: Database, tenant_id: str) -> None:
        self.env, self.db, self.tid = env, db, uuid.UUID(tenant_id)
        self.sender = SimulatedSender()

    def handle(self, event: CheckoutEvent, now: datetime = NOW) -> None:
        with self.db.tx(tenant_id=self.tid) as conn:
            handle_event(conn, self.tid, event, now=now)

    def run(self, now: datetime) -> Any:
        return run_due_steps(self.db, self.env.box, self.sender, now=now)

    def steps(self, ref: str | None = None) -> list[dict[str, Any]]:
        with self.db.tx(tenant_id=self.tid) as conn:
            return conn.execute(
                "SELECT s.step_no, s.status, s.detail, s.scheduled_at, c.external_ref "
                "FROM recovery_steps s JOIN recovery_cases c ON c.id = s.case_id "
                "WHERE (%s::text IS NULL OR c.external_ref = %s) "
                "ORDER BY c.external_ref, s.step_no",
                (ref, ref),
            ).fetchall()

    def case(self, ref: str) -> dict[str, Any]:
        with self.db.tx(tenant_id=self.tid) as conn:
            row = conn.execute(
                "SELECT * FROM recovery_cases WHERE external_ref = %s", (ref,)
            ).fetchone()
        assert row is not None
        return dict(row)

    def settings(self, **changes: Any) -> None:
        cols = ", ".join(f"{k} = %s" for k in changes)
        self.env.sql(
            f"UPDATE tenant_settings SET {cols} WHERE tenant_id = %s",
            (*changes.values(), str(self.tid)),
        )


@pytest.fixture
def ctx(env: Env, db: Database, client: TestClient) -> Ctx:
    # O worker é global: limpa passos de outros testes para medir só este cenário.
    env.sql("DELETE FROM recovery_steps")
    env.sql("DELETE FROM recovery_cases")
    email = unique_email("rec")
    tid = env.tenant("Loja Recuperacao", email)
    assert login(client, email).status_code == 200
    res = client.put(
        "/v1/connections/whatsapp_cloud",
        json={
            "values": {"phone_number_id": "1", "waba_id": "2", "access_token": "EAAGtoken123456"}
        },
    )
    assert res.status_code == 200, res.text
    env.sql("UPDATE connections SET status = 'connected' WHERE tenant_id = %s", (tid,))
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
    return Ctx(env, db, tid)


def test_cart_event_opens_case_and_schedules_default_steps(ctx: Ctx) -> None:
    ctx.handle(ev())
    steps = ctx.steps()
    assert [s["step_no"] for s in steps] == [1, 2, 3]
    assert steps[0]["scheduled_at"] == NOW + timedelta(minutes=30)
    assert steps[1]["scheduled_at"] == NOW + timedelta(days=1)
    assert ctx.case("r1")["status"] == "open"


def test_repeated_event_does_not_duplicate_case(ctx: Ctx) -> None:
    ctx.handle(ev())
    ctx.handle(ev())
    assert len(ctx.steps()) == 3


@pytest.mark.parametrize(
    "setup",
    ["disabled", "no_consent", "no_phone", "suppressed", "sequence_off", "unknown_kind"],
)
def test_no_case_when_conditions_fail(ctx: Ctx, setup: str) -> None:
    event = ev()
    if setup == "disabled":
        ctx.settings(recovery_enabled=False)
    elif setup == "no_consent":
        ctx.settings(consent_declared_at=None)
    elif setup == "no_phone":
        event = ev(phone=None)
    elif setup == "suppressed":
        ctx.env.sql(
            "INSERT INTO suppressions (tenant_id, identity, reason) VALUES (%s, %s, 'opt_out')",
            (str(ctx.tid), PHONE),
        )
    elif setup == "sequence_off":
        ctx.env.sql(
            "INSERT INTO recovery_sequences (tenant_id, trigger_kind, enabled, steps) "
            "VALUES (%s, 'abandoned_cart', false, '[]')",
            (str(ctx.tid),),
        )
    elif setup == "unknown_kind":
        event = ev(kind=n.REFUNDED)
    ctx.handle(event)
    assert ctx.steps() == []


def test_custom_sequence_and_max_contacts(ctx: Ctx) -> None:
    ctx.env.sql(
        "INSERT INTO recovery_sequences (tenant_id, trigger_kind, steps) "
        "VALUES (%s, %s, %s::jsonb)",
        (
            str(ctx.tid),
            "abandoned_cart",
            '[{"delay_minutes": 5, "template_key": "carrinho_1"},'
            ' {"delay_minutes": 10, "template_key": "carrinho_2"}]',
        ),
    )
    ctx.settings(max_contacts_per_case=1)
    ctx.handle(ev())
    assert [s["step_no"] for s in ctx.steps()] == [1]


def test_quiet_hours_push_scheduling_to_morning(ctx: Ctx) -> None:
    late = datetime(2026, 10, 5, 23, 40, tzinfo=UTC)  # 20h40 em SP; passo 1 cairia às 21h10
    ctx.handle(ev(), now=late)
    first = ctx.steps()[0]["scheduled_at"]
    assert first == datetime(2026, 10, 6, 11, 0, tzinfo=UTC)  # 8h em São Paulo


def test_worker_sends_only_due_steps_with_rendered_message(ctx: Ctx) -> None:
    ctx.handle(ev())
    assert ctx.run(NOW + timedelta(minutes=10)).claimed == 0
    stats = ctx.run(NOW + timedelta(minutes=31))
    assert (stats.claimed, stats.sent) == (1, 1)
    msg = ctx.sender.sent[0]
    assert msg.to_phone == PHONE
    assert (
        "Oi, Ana!" in msg.body and "Curso" in msg.body and "https://pay.example.test/c" in msg.body
    )
    assert ctx.steps()[0]["status"] == "sent"
    assert ctx.case("r1")["status"] == "open"
    # rodar de novo não reenvia
    assert ctx.run(NOW + timedelta(minutes=32)).claimed == 0
    assert len(ctx.sender.sent) == 1


def test_purchase_after_contact_marks_recovered_and_cancels_rest(ctx: Ctx) -> None:
    ctx.handle(ev())
    ctx.run(NOW + timedelta(minutes=31))
    ctx.handle(ev(kind=n.PURCHASE_APPROVED, ref="pay-1", cents=9700), now=NOW + timedelta(hours=1))
    case = ctx.case("r1")
    assert case["status"] == "recovered" and case["recovered_amount_cents"] == 9700
    assert [s["status"] for s in ctx.steps()] == ["sent", "canceled", "canceled"]
    assert ctx.run(NOW + timedelta(days=5)).claimed == 0


def test_purchase_without_contact_is_purchased_not_recovered(ctx: Ctx) -> None:
    ctx.handle(ev())
    ctx.handle(ev(kind=n.PURCHASE_APPROVED, ref="pay-2"), now=NOW + timedelta(minutes=5))
    case = ctx.case("r1")
    assert case["status"] == "purchased" and case["recovered_amount_cents"] is None


def test_purchase_of_other_product_keeps_case_open(ctx: Ctx) -> None:
    ctx.handle(ev(product="Curso"))
    ctx.handle(ev(kind=n.PURCHASE_APPROVED, ref="pay-3", product="Outro"), now=NOW)
    assert ctx.case("r1")["status"] == "open"


def test_late_purchase_after_sequence_end_still_counts_within_window(ctx: Ctx) -> None:
    ctx.handle(ev())
    for days in (0, 1, 3):
        ctx.run(NOW + timedelta(days=days, minutes=31))
    assert ctx.case("r1")["status"] == "exhausted"
    ctx.handle(ev(kind=n.PURCHASE_APPROVED, ref="pay-4"), now=NOW + timedelta(days=5))
    assert ctx.case("r1")["status"] == "recovered"
    ctx.handle(ev(kind=n.PURCHASE_APPROVED, ref="pay-5", product="Curso"), now=NOW)


def test_template_not_approved_skips_step(ctx: Ctx) -> None:
    ctx.env.sql(
        "UPDATE message_templates SET meta_status = 'submitted' "
        "WHERE tenant_id = %s AND key = 'carrinho_1'",
        (str(ctx.tid),),
    )
    ctx.handle(ev())
    stats = ctx.run(NOW + timedelta(minutes=31))
    assert stats.skipped == 1 and ctx.sender.sent == []
    assert ctx.steps()[0]["detail"] == "template_nao_aprovado"


def test_channel_not_connected_skips(ctx: Ctx) -> None:
    ctx.env.sql(
        "UPDATE connections SET status = 'needs_attention' WHERE tenant_id = %s", (str(ctx.tid),)
    )
    ctx.handle(ev())
    ctx.run(NOW + timedelta(minutes=31))
    assert ctx.sender.sent == [] and ctx.steps()[0]["detail"] == "canal_nao_conectado"


def test_recovery_turned_off_after_scheduling_blocks_send(ctx: Ctx) -> None:
    ctx.handle(ev())
    ctx.settings(recovery_enabled=False)
    ctx.run(NOW + timedelta(minutes=31))
    assert ctx.sender.sent == [] and ctx.steps()[0]["detail"] == "recuperacao_desligada"


def test_opt_out_after_scheduling_stops_case(ctx: Ctx) -> None:
    ctx.handle(ev())
    ctx.env.sql(
        "INSERT INTO suppressions (tenant_id, identity, reason) VALUES (%s, %s, 'opt_out')",
        (str(ctx.tid), PHONE),
    )
    ctx.run(NOW + timedelta(minutes=31))
    assert ctx.sender.sent == []
    case = ctx.case("r1")
    assert case["status"] == "stopped" and case["closed_reason"] == "opt_out"
    assert {s["status"] for s in ctx.steps()} <= {"skipped", "canceled"}


def test_quiet_hours_at_send_time_reschedules(ctx: Ctx) -> None:
    ctx.handle(ev())
    at_night = datetime(2026, 10, 6, 1, 0, tzinfo=UTC)  # 22h em SP
    stats = ctx.run(at_night)
    assert stats.rescheduled >= 1 and ctx.sender.sent == []
    step = ctx.steps()[0]
    assert step["status"] == "scheduled"
    assert step["scheduled_at"] == datetime(2026, 10, 6, 11, 0, tzinfo=UTC)


def test_daily_cap_defers_second_case_to_next_day(ctx: Ctx) -> None:
    ctx.handle(ev(ref="a", product="Curso A"))
    ctx.handle(ev(ref="b", product="Curso B"))
    stats = ctx.run(NOW + timedelta(minutes=31))
    assert stats.sent == 1 and stats.rescheduled == 1
    deferred = [s for s in ctx.steps() if s["detail"] == "limite_diario"]
    assert deferred[0]["scheduled_at"] == datetime(2026, 10, 6, 11, 0, tzinfo=UTC)


def test_new_case_for_same_product_supersedes_old(ctx: Ctx) -> None:
    ctx.handle(ev(ref="cart", product="Curso"))
    ctx.handle(ev(kind=n.PIX_PENDING, ref="pix", product="Curso"), now=NOW + timedelta(minutes=2))
    old = ctx.case("cart")
    assert old["status"] == "stopped" and old["closed_reason"] == "superseded"
    assert all(s["status"] == "canceled" for s in ctx.steps("cart"))
    assert ctx.case("pix")["status"] == "open"


def test_send_failure_is_final_and_not_retried(ctx: Ctx) -> None:
    ctx.sender.fail_with = "número inválido"
    ctx.handle(ev())
    assert ctx.run(NOW + timedelta(minutes=31)).failed == 1
    ctx.sender.fail_with = None
    ctx.run(NOW + timedelta(minutes=40))
    assert ctx.sender.sent == []
    assert ctx.steps()[0]["status"] == "failed"


def test_unknown_send_outcome_is_not_retried(ctx: Ctx) -> None:
    class Boom(SimulatedSender):
        def send(self, config: dict[str, str], message: Any) -> str:
            raise TimeoutError("sem resposta")

    ctx.sender = Boom()
    ctx.handle(ev())
    ctx.run(NOW + timedelta(minutes=31))
    step = ctx.steps()[0]
    assert step["status"] == "failed" and "resultado_incerto" in step["detail"]


def test_unavailable_sender_claims_nothing(ctx: Ctx) -> None:
    ctx.handle(ev())
    stats = run_due_steps(ctx.db, ctx.env.box, UnavailableSender(), now=NOW + timedelta(days=9))
    assert stats.claimed == 0
    assert all(s["status"] == "scheduled" for s in ctx.steps())


def test_stuck_sending_step_becomes_failed_never_resent(ctx: Ctx) -> None:
    ctx.handle(ev())
    ctx.env.sql(
        "UPDATE recovery_steps SET status = 'sending', claimed_at = %s WHERE tenant_id = %s "
        "AND step_no = 1",
        (NOW, str(ctx.tid)),
    )
    assert reap_stuck(ctx.db, NOW + timedelta(hours=2)) == 1
    assert ctx.steps()[0]["status"] == "failed"
    assert ctx.sender.sent == []


def test_cases_are_isolated_between_tenants(ctx: Ctx, env: Env, db: Database) -> None:
    ctx.handle(ev())
    other = uuid.UUID(env.tenant("Outra", unique_email("x")))
    with db.tx(tenant_id=other) as conn:
        assert conn.execute("SELECT 1 FROM recovery_cases").fetchall() == []
        assert conn.execute("SELECT 1 FROM recovery_steps").fetchall() == []
        assert conn.execute("SELECT 1 FROM contacts").fetchall() == []


def test_client_context_cannot_read_other_tenant_even_with_filter(ctx: Ctx, env: Env) -> None:
    ctx.handle(ev())
    with psycopg.connect(env.app_url) as conn:  # sem contexto nenhum: RLS nega tudo
        assert conn.execute("SELECT count(*) FROM recovery_cases").fetchone() == (0,)


def test_webhook_to_case_end_to_end(env: Env, db: Database, ctx: Ctx, client: TestClient) -> None:
    ctx.env.sql("DELETE FROM recovery_steps WHERE tenant_id = %s", (str(ctx.tid),))
    res = client.put("/v1/connections/cakto", json={"values": {"webhook_secret": "seg-e2e-123"}})
    assert res.status_code == 200, res.text
    pid = res.json()["webhook_url"].rsplit("/", 1)[1]
    body = {
        "event": "checkout_abandonment",
        "secret": "seg-e2e-123",
        "data": {
            "id": "ck-e2e",
            "customer": {"name": "Ana", "email": "ana@example.test", "phone": "11999998888"},
            "product": {"id": "p1", "name": "Curso"},
            "amount": 97,
        },
    }
    out = client.post(f"/v1/webhooks/cakto/{pid}", json=body)
    assert out.json() == {"status": "accepted"}
    assert ctx.case("ck-e2e")["status"] == "open"
