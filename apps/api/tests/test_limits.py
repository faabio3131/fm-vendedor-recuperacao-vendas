"""Limites: uso da IA por plano (dado) e contatos novos por dia por número (Meta)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from fm_seller.ai.model import AiReply, SimulatedAiModel
from fm_seller.ai.seller import run_ai_replies
from fm_seller.db import Database
from tests.conftest import Env, login, unique_email
from tests.test_ai_seller import Setup
from tests.test_ai_seller import s as s
from tests.test_recovery_engine import NOW, Ctx, ev
from tests.test_recovery_engine import ctx as ctx

OK = AiReply("Olá! Como posso ajudar?", tokens_in=120, tokens_out=15)


def _plan(env: Env, key: str, limits: str) -> None:
    env.sql(
        "INSERT INTO plans (key, display_name, phase, features, limits) "
        "SELECT %s, 'Plano de teste', 1, features, %s::jsonb FROM plans WHERE key = 'fase-1' "
        "ON CONFLICT (key) DO UPDATE SET limits = EXCLUDED.limits",
        (key, limits),
    )


def _use_plan(s: Setup, key: str) -> None:
    s.env.sql("UPDATE tenant_plans SET plan_key = %s WHERE tenant_id = %s", (key, s.tid))


def _usage(s: Setup) -> tuple[int, int, int, int]:
    row = s.env.sql(
        "SELECT coalesce(sum(calls),0), coalesce(sum(failures),0), coalesce(sum(tokens_in),0), "
        "coalesce(sum(tokens_out),0) FROM ai_usage WHERE tenant_id = %s",
        (s.tid,),
    )[0]
    return tuple(int(x) for x in row)  # type: ignore[return-value]


# ---------------------------------------------------------------- IA por plano


def test_ai_replies_are_counted_with_tokens(s: Setup, db: Database) -> None:
    s.say("oi")
    run_ai_replies(db, SimulatedAiModel(forced=OK))
    assert _usage(s) == (1, 0, 120, 15)


def test_plan_limit_hands_off_when_reached_and_keeps_counting_nothing_more(
    s: Setup,
    db: Database,
) -> None:
    _plan(s.env, "teste-limite-2", '{"ai_replies_per_month": 2}')
    _use_plan(s, "teste-limite-2")
    model = SimulatedAiModel(forced=OK)
    for text in ("um", "dois"):
        s.say(text)
        assert run_ai_replies(db, model).replied == 1
    s.say("três")
    stats = run_ai_replies(db, model)
    assert stats.handoffs == 1 and len(model.calls) == 2  # o modelo nem foi chamado
    assert s.conv_state() == ("human", "limite_do_plano")
    assert _usage(s)[0] == 2
    assert s.outbound()[-1][1].startswith("Certo! Vou chamar uma pessoa")


def test_limit_zero_blocks_everything_and_missing_limit_means_unlimited(
    s: Setup,
    db: Database,
) -> None:
    _plan(s.env, "teste-limite-0", '{"ai_replies_per_month": 0}')
    _use_plan(s, "teste-limite-0")
    s.say("oi")
    run_ai_replies(db, SimulatedAiModel(forced=OK))
    assert s.conv_state() == ("human", "limite_do_plano")
    # outro cliente, plano sem limite
    other = Setup(s.env, db)
    _plan(s.env, "teste-sem-limite", "{}")
    _use_plan(other, "teste-sem-limite")
    s.env.sql(
        "INSERT INTO ai_usage (tenant_id, day, calls) VALUES (%s, current_date, 100000)",
        (other.tid,),
    )
    other.say("oi")
    assert run_ai_replies(db, SimulatedAiModel(forced=OK)).replied == 1


def test_usage_from_last_month_does_not_count(s: Setup, db: Database) -> None:
    _plan(s.env, "teste-limite-1", '{"ai_replies_per_month": 1}')
    _use_plan(s, "teste-limite-1")
    s.env.sql(
        "INSERT INTO ai_usage (tenant_id, day, calls) "
        "VALUES (%s, (date_trunc('month', now()) - interval '1 day')::date, 50)",
        (s.tid,),
    )
    s.say("oi")
    assert run_ai_replies(db, SimulatedAiModel(forced=OK)).replied == 1


def test_model_failures_are_recorded_but_do_not_use_the_quota(
    s: Setup,
    db: Database,
) -> None:
    class Broken:
        available = True

        def reply(self, ctx: object) -> AiReply:
            raise RuntimeError("fora do ar")

    s.say("oi")
    run_ai_replies(db, Broken())
    assert _usage(s)[:2] == (0, 1)
    assert s.conv_state() == ("human", "erro_do_modelo")


def test_usage_endpoint(client: TestClient, env: Env) -> None:
    _plan(env, "teste-limite-4", '{"ai_replies_per_month": 4}')
    email = unique_email("use")
    tid = env.tenant("Loja Uso", email, plan="teste-limite-4")
    assert login(client, email).status_code == 200
    env.sql(
        "INSERT INTO ai_usage (tenant_id, day, calls, tokens_in) VALUES (%s, current_date, 1, 50)",
        (tid,),
    )
    got = client.get("/v1/seller/usage").json()
    assert got["replies"] == 1 and got["limit"] == 4 and got["percent"] == 25
    assert got["tokens_in"] == 50 and "body" not in str(got)


# ---------------------------------------------------------------- limite do número


def _two_contacts(ctx: Ctx, limit: int) -> None:
    ctx.settings(number_daily_limit=limit)
    ctx.handle(ev(ref="a", phone="5511999990001", email="a@example.test"))
    ctx.handle(ev(ref="b", phone="5511999990002", email="b@example.test"))


def test_number_daily_limit_defers_new_contacts_to_the_next_day(ctx: Ctx) -> None:
    _two_contacts(ctx, 1)
    ctx.run(NOW + timedelta(minutes=45))
    first = [s for s in ctx.steps() if s["step_no"] == 1]
    assert sorted(s["status"] for s in first) == [
        "scheduled",
        "sent",
    ]  # a ordem entre eles não importa
    deferred = next(s for s in first if s["status"] == "scheduled")
    assert deferred["detail"] == "limite_do_numero"
    assert deferred["scheduled_at"] > NOW + timedelta(hours=12)  # amanhã de manhã
    # no dia seguinte o contato adiado é atendido
    ctx.run(deferred["scheduled_at"] + timedelta(minutes=1))
    after = [s for s in ctx.steps() if s["step_no"] == 1]
    assert [s["status"] for s in after] == ["sent", "sent"]


def test_number_limit_not_reached_sends_everyone(ctx: Ctx) -> None:
    _two_contacts(ctx, 2)
    ctx.run(NOW + timedelta(minutes=45))
    assert [s["status"] for s in ctx.steps() if s["step_no"] == 1] == ["sent", "sent"]


def test_contact_already_reached_today_is_not_blocked_by_the_number_limit(
    ctx: Ctx,
) -> None:
    ctx.settings(number_daily_limit=1, daily_cap=3)
    ctx.env.sql(
        "INSERT INTO recovery_sequences (tenant_id, trigger_kind, steps) VALUES (%s, "
        '\'abandoned_cart\', \'[{"delay_minutes":1,"template_key":"carrinho_1"},'
        '{"delay_minutes":2,"template_key":"carrinho_2"}]\')',
        (str(ctx.tid),),
    )
    ctx.handle(ev(ref="a", phone="5511999990001", email="a@example.test"))
    ctx.run(NOW + timedelta(minutes=5))
    assert [s["status"] for s in ctx.steps("a")] == ["sent", "sent"]


def test_number_limit_setting_is_validated_and_saved(client: TestClient, env: Env) -> None:
    email = unique_email("lim")
    env.tenant("Loja Limite", email)
    assert login(client, email).status_code == 200
    assert client.get("/v1/recovery/settings").json()["number_daily_limit"] == 200
    for bad in (0, -1, 100_001):
        assert (
            client.put("/v1/recovery/settings", json={"number_daily_limit": bad}).status_code == 422
        )
    ok = client.put("/v1/recovery/settings", json={"number_daily_limit": 250})
    assert ok.status_code == 200 and ok.json()["number_daily_limit"] == 250


@pytest.mark.parametrize("key", ["fase-1", "fase-2", "fase-3", "fase-4"])
def test_provisional_plan_limits_are_data(env: Env, key: str) -> None:
    assert env.sql("SELECT limits->>'ai_replies_per_month' FROM plans WHERE key = %s", (key,)) == [
        ("1000",)
    ]
