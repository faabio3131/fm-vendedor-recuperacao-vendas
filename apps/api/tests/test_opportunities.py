"""Oportunidades próprias: registro avulso, planilha, desfecho e conversa que esfriou."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fm_seller.db import Database
from fm_seller.recovery.cold import detect_cold_conversations
from fm_seller.recovery.opportunities import parse_amount
from tests.conftest import Env, connect_whatsapp, login, unique_email
from tests.test_whatsapp_inbound import PHONE as WA_PHONE
from tests.test_whatsapp_inbound import payload, post, text
from tests.test_whatsapp_inbound import wa as wa

PHONE = "11999991111"
OK = {"phone": PHONE, "name": "Ana", "product": "Sofá", "amount_cents": 150000}


@pytest.fixture(autouse=True)
def _isolate(env: Env) -> None:
    # Detecção e worker são globais: desliga o que outros testes deixaram ligado.
    env.sql("UPDATE tenant_settings SET cold_enabled = false")


def _owner(client: TestClient, env: Env, enable: bool = True) -> str:
    email = unique_email("opp")
    tid = env.tenant("Loja Oportunidades", email)
    assert login(client, email).status_code == 200
    connect_whatsapp(client, env, tid)
    if enable:
        res = client.put(
            "/v1/recovery/settings", json={"consent_declared": True, "recovery_enabled": True}
        )
        assert res.status_code == 200, res.text
    return tid


def _create(client: TestClient, **over: Any) -> Any:
    return client.post(
        "/v1/recovery/opportunities", json={**OK, "contact_authorized": True, **over}
    )


# ---------------------------------------------------------------- registro avulso


def test_manual_needs_authorization_and_recovery_on(client: TestClient, env: Env) -> None:
    tid = _owner(client, env, enable=False)
    off = _create(client)
    assert off.status_code == 400 and off.json()["error"]["code"] == "recovery_off"
    no_auth = client.post("/v1/recovery/opportunities", json=OK)
    assert no_auth.json()["error"]["code"] == "authorization_required"
    connect_whatsapp(client, env, tid)
    client.put("/v1/recovery/settings", json={"consent_declared": True, "recovery_enabled": True})
    assert _create(client).status_code == 201


def test_manual_opens_case_with_default_quote_steps(client: TestClient, env: Env) -> None:
    tid = _owner(client, env)
    res = _create(client, payment_url="https://pay.example.test/x", note="pediu desconto")
    assert res.status_code == 201
    case = next(c for c in client.get("/v1/recovery/cases").json() if c["id"] == res.json()["id"])
    assert case["source"] == "manual" and case["trigger"] == "quote_pending"
    assert case["product"] == "Sofá" and case["amount_cents"] == 150000
    assert case["contact"]["phone"].endswith("1111") and "999991111" not in str(case)
    steps = env.sql(
        "SELECT template_key FROM recovery_steps WHERE case_id = %s ORDER BY step_no",
        (res.json()["id"],),
    )
    assert [s[0] for s in steps] == ["orcamento_1", "orcamento_2", "orcamento_3"]
    assert env.sql("SELECT count(*) FROM audit_log WHERE tenant_id = %s", (tid,))[0][0] >= 1


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"phone": "123"}, "no_phone"),
        ({"payment_url": "http://inseguro.test"}, "invalid_link"),
        ({"payment_url": "https://a b.test"}, "invalid_link"),
    ],
)
def test_manual_validation(client: TestClient, env: Env, over: dict[str, Any], code: str) -> None:
    _owner(client, env)
    res = _create(client, **over)
    assert res.status_code == 400 and res.json()["error"]["code"] == code


def test_manual_rejects_negative_amount_and_blocked_contact(client: TestClient, env: Env) -> None:
    tid = _owner(client, env)
    assert _create(client, amount_cents=-1).status_code == 422
    env.sql(
        "INSERT INTO suppressions (tenant_id, identity, reason) VALUES (%s, %s, 'opt_out')",
        (tid, "55" + PHONE),
    )
    res = _create(client)
    assert res.status_code == 400 and res.json()["error"]["code"] == "suppressed"


def test_same_product_new_opportunity_replaces_the_previous_one(
    client: TestClient, env: Env
) -> None:
    _owner(client, env)
    first = _create(client).json()["id"]
    second = _create(client).json()["id"]
    cases = {c["id"]: c for c in client.get("/v1/recovery/cases").json()}
    assert cases[first]["status"] == "stopped" and cases[second]["status"] == "open"


def test_agent_can_register_but_not_change_settings(client: TestClient, env: Env) -> None:
    tid = _owner(client, env)
    agent = unique_email("agent")
    env.invite(tid, agent, "agent")
    client.post("/v1/auth/logout")
    assert login(client, agent).status_code == 200
    assert _create(client).status_code == 201
    assert client.put("/v1/recovery/settings", json={"daily_cap": 2}).status_code == 403


# ---------------------------------------------------------------- desfecho


def _outcome(client: TestClient, case_id: str, outcome: str, **extra: Any) -> Any:
    return client.post(f"/v1/recovery/cases/{case_id}/outcome", json={"outcome": outcome, **extra})


def test_sold_without_any_message_is_a_normal_purchase_not_recovery(
    client: TestClient, env: Env
) -> None:
    _owner(client, env)
    cid = _create(client).json()["id"]
    res = _outcome(client, cid, "sold")
    assert res.json() == {"id": cid, "status": "purchased", "recovered_cents": None}
    assert (
        env.sql("SELECT status FROM recovery_steps WHERE case_id = %s", (cid,))
        == [("canceled",)] * 3
    )


def test_sold_after_a_message_counts_as_recovered(client: TestClient, env: Env) -> None:
    _owner(client, env)
    cid = _create(client).json()["id"]
    env.sql(
        "UPDATE recovery_steps SET status = 'sent', sent_at = now() "
        "WHERE case_id = %s AND step_no = 1",
        (cid,),
    )
    res = _outcome(client, cid, "sold", amount_cents=140000)
    assert res.json()["status"] == "recovered" and res.json()["recovered_cents"] == 140000
    summary = client.get("/v1/recovery/summary").json()
    assert summary["recovered_cents"] >= 140000


def test_lost_stops_the_sequence_and_closed_case_cannot_change(
    client: TestClient, env: Env
) -> None:
    _owner(client, env)
    cid = _create(client).json()["id"]
    assert _outcome(client, cid, "lost").json()["status"] == "stopped"
    assert (
        env.sql(
            "SELECT count(*) FROM recovery_steps WHERE case_id = %s AND status = 'scheduled'",
            (cid,),
        )[0][0]
        == 0
    )
    again = _outcome(client, cid, "sold")
    assert again.status_code == 409 and again.json()["error"]["code"] == "case_closed"
    assert _outcome(client, cid, "talvez").status_code == 400


def test_outcome_of_another_tenants_case_is_not_found(client: TestClient, env: Env) -> None:
    _owner(client, env)
    cid = _create(client).json()["id"]
    client.post("/v1/auth/logout")
    _owner(client, env)
    assert _outcome(client, cid, "lost").status_code == 404
    assert _outcome(client, str(uuid.uuid4()), "lost").status_code == 404


# ---------------------------------------------------------------- planilha


@pytest.mark.parametrize(
    ("raw", "cents"),
    [
        ("R$ 1.234,56", 123456),
        ("1234.56", 123456),
        ("49,9", 4990),
        ("1.234", 123400),
        ("12.50", 1250),
        ("", 0),
        ("0", 0),
    ],
)
def test_parse_amount(raw: str, cents: int) -> None:
    assert parse_amount(raw) == cents


@pytest.mark.parametrize("raw", ["abc", "-5", "R$ 999999999999"])
def test_parse_amount_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_amount(raw)


SHEET = (
    "Nome;Telefone;Produto;Valor;Link\n"
    "Ana;(11) 99999-1111;Sofá;R$ 1.234,56;https://pay.example.test/a\n"
    "Bia;123;Mesa;50;\n"
    "Caio;11999992222;Cadeira;49,90;http://inseguro\n"
    "Duda;11999993333;Painel;80,00;\n"
)


def _import(client: TestClient, csv_text: str, authorized: bool = True) -> Any:
    return client.post(
        "/v1/recovery/opportunities/import",
        json={"csv": csv_text, "contact_authorized": authorized},
    )


def test_import_creates_valid_rows_reports_bad_ones_and_is_repeatable(
    client: TestClient, env: Env
) -> None:
    _owner(client, env)
    first = _import(client, SHEET).json()
    assert first["total"] == 4 and first["created"] == 2 and first["errors_total"] == 2
    assert {e["line"] for e in first["errors"]} == {3, 4}
    cases = [c for c in client.get("/v1/recovery/cases").json() if c["source"] == "importacao"]
    assert {c["amount_cents"] for c in cases} == {123456, 8000}
    again = _import(client, SHEET).json()
    assert again["created"] == 0
    assert again["skipped"] == [
        {"reason": "duplicate", "message": "Esta oportunidade já estava registrada.", "count": 2}
    ]


def test_import_accepts_comma_tab_and_bom(client: TestClient, env: Env) -> None:
    _owner(client, env)
    comma = "﻿nome,telefone,produto\nAna,11999994444,Item A\n"
    tab = "nome\ttelefone\tproduto\nBia\t11999995555\tItem B\n"
    assert _import(client, comma).json()["created"] == 1
    assert _import(client, tab).json()["created"] == 1
    assert _import(client, "telefone\n11999996666\n").json()["created"] == 1  # só o telefone


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("nome;produto\nAna;X\n", "csv_no_phone"),
        ("telefone;nome\n", "csv_empty"),
        ("so uma linha sem colunas", "csv_no_phone"),
        pytest.param("telefone\n" + "11999990000\n" * 201, "csv_too_many", id="muitas-linhas"),
    ],
)
def test_import_rejects_bad_files(client: TestClient, env: Env, body: str, code: str) -> None:
    _owner(client, env)
    res = _import(client, body)
    assert res.status_code == 400 and res.json()["error"]["code"] == code


def test_import_needs_authorization_and_never_writes_when_off(client: TestClient, env: Env) -> None:
    _owner(client, env)
    assert _import(client, SHEET, authorized=False).json()["error"]["code"] == (
        "authorization_required"
    )
    client.put("/v1/recovery/settings", json={"recovery_enabled": False})
    assert _import(client, SHEET).json()["error"]["code"] == "recovery_off"
    assert client.get("/v1/recovery/cases").json() == []


# ---------------------------------------------------------------- conversa que esfriou

NOW = datetime.now(UTC)


def _seed(
    env: Env,
    tid: str,
    *,
    phone: str = WA_PHONE,
    status: str = "bot",
    last_author: str = "bot",
    last_dir: str = "out",
    hours_ago: int = 5,
    out_status: str = "sent",
) -> str:
    contact = env.sql(
        "INSERT INTO contacts (tenant_id, name, phone) VALUES (%s, 'Carla', %s) "
        "ON CONFLICT DO NOTHING RETURNING id",
        (tid, phone),
    )
    cid = (
        contact[0][0]
        if contact
        else env.sql("SELECT id FROM contacts WHERE tenant_id = %s AND phone = %s", (tid, phone))[
            0
        ][0]
    )
    conv = env.sql(
        "INSERT INTO conversations (tenant_id, contact_id, status, last_inbound_at) "
        "VALUES (%s, %s, %s, now() - make_interval(hours => %s)) RETURNING id",
        (tid, cid, status, hours_ago + 1),
    )[0][0]
    env.sql(
        "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status, "
        "created_at) VALUES (%s, %s, 'in', 'customer', 'quanto custa o sofá?', 'received', "
        "now() - make_interval(hours => %s))",
        (tid, conv, hours_ago + 1),
    )
    env.sql(
        "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status, "
        "created_at) VALUES (%s, %s, %s, %s, 'custa R$ 1.500', %s, "
        "now() - make_interval(hours => %s))",
        (
            tid,
            conv,
            last_dir,
            last_author,
            out_status if last_dir == "out" else "received",
            hours_ago,
        ),
    )
    return str(conv)


def _cold(client: TestClient, env: Env, on: bool = True) -> str:
    tid = _owner(client, env)
    if on:
        res = client.put("/v1/recovery/settings", json={"cold_enabled": True})
        assert res.status_code == 200 and res.json()["cold_after_hours"] == 3
    return tid


def test_cold_conversation_opens_one_case_and_not_again(
    client: TestClient, env: Env, db: Database
) -> None:
    tid = _cold(client, env)
    _seed(env, tid)
    assert detect_cold_conversations(db) == 1
    assert detect_cold_conversations(db) == 0  # já tem caso aberto
    case = [c for c in client.get("/v1/recovery/cases").json() if c["source"] == "conversa"]
    assert len(case) == 1 and case[0]["trigger"] == "conversation_cold"
    keys = env.sql(
        "SELECT template_key FROM recovery_steps s JOIN recovery_cases c ON c.id = s.case_id "
        "WHERE c.id = %s ORDER BY step_no",
        (case[0]["id"],),
    )
    assert [k[0] for k in keys] == ["conversa_1", "conversa_2"]
    _outcome(client, case[0]["id"], "lost")
    assert detect_cold_conversations(db) == 0  # descansa antes de tentar o mesmo contato


@pytest.mark.parametrize(
    "scenario",
    [
        "disabled",
        "customer_spoke_last",
        "too_recent",
        "too_old",
        "waiting_for_human",
        "failed_send",
        "recovery_message_last",
        "suppressed",
        "sequence_off",
    ],
)
def test_cold_conversation_is_ignored_when(
    client: TestClient, env: Env, db: Database, scenario: str
) -> None:
    tid = _cold(client, env, on=scenario != "disabled")
    kw: dict[str, Any] = {}
    if scenario == "customer_spoke_last":
        kw = {"last_dir": "in", "last_author": "customer"}
    elif scenario == "too_recent":
        kw = {"hours_ago": 1}
    elif scenario == "too_old":
        kw = {"hours_ago": 24 * 4}
    elif scenario == "waiting_for_human":
        kw = {"status": "human"}  # a última fala foi da IA passando a vez: a loja deve resposta
    elif scenario == "failed_send":
        kw = {"out_status": "failed"}
    elif scenario == "recovery_message_last":
        kw = {"last_author": "recovery"}
    _seed(env, tid, **kw)
    if scenario == "suppressed":
        env.sql(
            "INSERT INTO suppressions (tenant_id, identity, reason) VALUES (%s, %s, 'manual')",
            (tid, WA_PHONE),
        )
    if scenario == "sequence_off":
        client.put(
            "/v1/recovery/sequences/conversation_cold",
            json={"enabled": False, "steps": [{"delay_minutes": 5, "template_key": "x"}]},
        )
    assert detect_cold_conversations(db) == 0


def test_cold_conversation_answered_by_a_person_counts(
    client: TestClient, env: Env, db: Database
) -> None:
    tid = _cold(client, env)
    _seed(env, tid, status="human", last_author="human")
    assert detect_cold_conversations(db) == 1


def test_cold_waits_for_the_configured_hours(client: TestClient, env: Env, db: Database) -> None:
    tid = _cold(client, env)
    client.put("/v1/recovery/settings", json={"cold_after_hours": 8})
    _seed(env, tid, hours_ago=5)
    assert detect_cold_conversations(db) == 0
    assert client.put("/v1/recovery/settings", json={"cold_after_hours": 99}).status_code == 422


def test_customer_writing_again_stops_the_cold_case(
    wa: tuple[TestClient, str, str, str],
    env: Env,
    db: Database,
) -> None:
    c, tid, pid, _ = wa
    env.sql("UPDATE connections SET status = 'connected' WHERE tenant_id = %s", (tid,))
    assert (
        c.put(
            "/v1/recovery/settings",
            json={"consent_declared": True, "recovery_enabled": True, "cold_enabled": True},
        ).status_code
        == 200
    )
    _seed(env, tid)
    assert detect_cold_conversations(db) == 1
    out = post(c, pid, payload([text("wamid.cold1", "ainda tenho interesse")]))
    assert out.status_code == 200
    rows = env.sql("SELECT status, closed_reason FROM recovery_cases WHERE tenant_id = %s", (tid,))
    assert rows == [("stopped", "cliente_respondeu")]
    assert (
        env.sql(
            "SELECT count(*) FROM recovery_steps WHERE tenant_id = %s AND status = 'scheduled'",
            (tid,),
        )[0][0]
        == 0
    )
