"""Relatórios de recuperação: funil, valor recuperado, agrupamentos, CSV seguro e isolamento."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fm_seller.errors import AppError
from fm_seller.recovery import reports
from tests.conftest import Env, login, unique_email

START, END = "2026-09-01", "2026-09-30"
FORMULA = '=HYPERLINK("http://golpe.example","clique")'


def at(day: str, hour: int = 15) -> datetime:
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hour, 0, tzinfo=UTC)


class Data:
    """Cliente com dados de recuperação escritos direto no banco (sem passar pelo motor)."""

    def __init__(self, env: Env, client: TestClient, role: str = "owner") -> None:
        self.env, self.client = env, client
        self.email = unique_email("rep")
        self.tid = env.tenant("Loja Relatorio", self.email, role=role)
        self.n = 0

    def login(self) -> None:
        assert login(self.client, self.email).status_code == 200

    def contact(self, phone: str) -> str:
        return str(
            self.env.sql(
                "INSERT INTO contacts (tenant_id, name, phone) VALUES (%s, 'Contato', %s) "
                "RETURNING id",
                (self.tid, phone),
            )[0][0]
        )

    def case(
        self,
        contact: str,
        status: str,
        day: str,
        *,
        product: str = "Curso X",
        kind: str = "abandoned_cart",
        source: str = "checkout",
        recovered: int | None = None,
        hour: int = 15,
        closed: datetime | None = None,
    ) -> str:
        self.n += 1
        return str(
            self.env.sql(
                "INSERT INTO recovery_cases (tenant_id, contact_id, trigger_kind, external_ref, "
                "product_name, amount_cents, status, recovered_amount_cents, opened_at, "
                "closed_at, source) VALUES (%s, %s, %s, %s, %s, 9700, %s, %s, %s, %s, %s) "
                "RETURNING id",
                (
                    self.tid,
                    contact,
                    kind,
                    f"ref-{uuid.uuid4().hex[:8]}-{self.n}",
                    product,
                    status,
                    recovered,
                    at(day, hour),
                    closed,
                    source,
                ),
            )[0][0]
        )

    def step(
        self,
        case: str,
        no: int,
        status: str = "sent",
        delivery: str | None = None,
        sent: datetime | None = None,
    ) -> None:
        self.env.sql(
            "INSERT INTO recovery_steps (tenant_id, case_id, step_no, template_key, "
            "scheduled_at, first_scheduled_at, status, sent_at, delivery_status) "
            "VALUES (%s, %s, %s, 'carrinho_1', now(), now(), %s, %s, %s)",
            (self.tid, case, no, status, sent if status == "sent" else None, delivery),
        )

    def say(self, contact: str, direction: str, when: datetime, channel: str = "whatsapp") -> None:
        conv = self.env.sql(
            "INSERT INTO conversations (tenant_id, contact_id, channel) VALUES (%s, %s, %s) "
            "ON CONFLICT (tenant_id, contact_id, channel) "
            "DO UPDATE SET status = conversations.status RETURNING id",
            (self.tid, contact, channel),
        )[0][0]
        author = "customer" if direction == "in" else "bot"
        self.env.sql(
            "INSERT INTO messages (tenant_id, conversation_id, direction, author, body, status, "
            "created_at) VALUES (%s, %s, %s, %s, 'oi', %s, %s)",
            (self.tid, conv, direction, author, "received" if direction == "in" else "sent", when),
        )

    def get(self, group_by: str = "none", start: str = START, end: str = END) -> dict[str, Any]:
        res = self.client.get(
            "/v1/reports/recovery", params={"from": start, "to": end, "group_by": group_by}
        )
        assert res.status_code == 200, res.text
        return dict(res.json())


def scenario(env: Env, client: TestClient) -> Data:
    """Cinco casos: duas recuperadas, uma compra sem mensagem, uma parada e uma aberta."""
    d = Data(env, client)
    a, b, c, dd, e = (d.contact(f"55119000000{i}") for i in range(5))
    first = at("2026-09-10", 15)
    c1 = d.case(a, "recovered", "2026-09-10", recovered=9700)
    d.step(c1, 1, delivery="read", sent=first)
    d.step(c1, 2, delivery="delivered", sent=at("2026-09-11", 15))
    d.say(a, "in", at("2026-09-10", 16))  # respondeu depois da primeira mensagem
    c2 = d.case(b, "recovered", "2026-09-10", kind="pix_pending", recovered=5000)
    d.step(c2, 1, delivery="sent", sent=first)
    c3 = d.case(c, "purchased", "2026-09-11", product="Curso Y")
    d.step(c3, 1, status="skipped")
    c4 = d.case(
        dd,
        "stopped",
        "2026-09-12",
        product="Curso Y",
        kind="conversation_cold",
        source="conversa",
        closed=at("2026-09-13", 12),
    )
    d.step(c4, 1, delivery="failed", sent=at("2026-09-12", 16))
    d.say(dd, "in", at("2026-09-12", 18))
    c5 = d.case(e, "open", "2026-09-12", product=FORMULA)
    d.step(c5, 1, status="scheduled")
    d.login()
    return d


@pytest.fixture
def rep(env: Env, client: TestClient) -> Data:
    return scenario(env, client)


# ------------------------------------------------------------------- números


def test_total_funnel_numbers(rep: Data) -> None:
    t = rep.get()["total"]
    assert (t["cases"], t["with_message"], t["messages_sent"]) == (5, 3, 4)
    assert (t["delivered"], t["read"], t["replied"]) == (2, 1, 2)
    assert (t["recovered"], t["recovered_cents"]) == (2, 14700)
    assert (t["purchased_without_message"], t["stopped"], t["exhausted"], t["open"]) == (1, 1, 0, 1)
    assert t["recovery_rate"] == round(2 / 3, 4) and t["reply_rate"] == round(2 / 3, 4)


def test_delivery_comes_from_the_provider_not_from_the_send(rep: Data) -> None:
    t = rep.get()["total"]
    # 4 mensagens enviadas, mas só 2 com entrega confirmada ("sent" e "failed" não contam)
    assert t["messages_sent"] == 4 and t["delivered"] == 2


def test_purchase_without_message_is_not_attributed(rep: Data) -> None:
    t = rep.get()["total"]
    assert t["purchased_without_message"] == 1
    assert t["recovered"] == 2  # a compra sem mensagem não entra nas recuperadas


def test_group_by_product(rep: Data) -> None:
    groups = {g["group"]: g for g in rep.get("product")["groups"]}
    assert set(groups) == {"Curso X", "Curso Y", FORMULA}
    x, y = groups["Curso X"], groups["Curso Y"]
    assert (x["cases"], x["messages_sent"], x["delivered"], x["read"]) == (2, 3, 2, 1)
    assert (x["recovered"], x["recovered_cents"], x["replied"]) == (2, 14700, 1)
    assert (y["cases"], y["with_message"], y["purchased_without_message"], y["stopped"]) == (
        2,
        1,
        1,
        1,
    )
    assert y["replied"] == 1 and groups[FORMULA]["open"] == 1


def test_group_by_sequence_source_and_day(rep: Data) -> None:
    seq = {g["group"]: g["cases"] for g in rep.get("sequence")["groups"]}
    assert seq == {"abandoned_cart": 3, "pix_pending": 1, "conversation_cold": 1}
    src = {g["group"]: g["cases"] for g in rep.get("source")["groups"]}
    assert src == {"checkout": 4, "conversa": 1}
    days = {g["group"]: g["cases"] for g in rep.get("day")["groups"]}
    assert days == {"2026-09-10": 2, "2026-09-11": 1, "2026-09-12": 2}
    for kind in ("product", "sequence", "source", "day"):
        report = rep.get(kind)
        assert sum(g["cases"] for g in report["groups"]) == report["total"]["cases"] == 5
        assert sum(g["recovered_cents"] for g in report["groups"]) == 14700


def test_reply_only_counts_after_the_first_message_and_inside_the_case(
    env: Env, client: TestClient
) -> None:
    d = Data(env, client)
    early, late, never = (d.contact(f"5511910000{i}") for i in range(3))
    for contact, when in ((early, at("2026-09-10", 14)), (late, at("2026-09-14", 12))):
        c = d.case(contact, "stopped", "2026-09-10", closed=at("2026-09-11", 12))
        d.step(c, 1, delivery="delivered", sent=at("2026-09-10", 15))
        d.say(contact, "in", when)  # antes da mensagem / depois de o caso fechar
    c = d.case(never, "exhausted", "2026-09-10", closed=at("2026-09-12", 12))
    d.step(c, 1, delivery="delivered", sent=at("2026-09-10", 15))
    d.say(never, "out", at("2026-09-10", 16))  # nossa mensagem não é resposta
    d.login()
    t = d.get()["total"]
    assert t["with_message"] == 3 and t["replied"] == 0 and t["reply_rate"] == 0.0


def test_period_uses_the_tenant_timezone(env: Env, client: TestClient) -> None:
    d = Data(env, client)
    contact = d.contact("5511920000000")
    # 02:30 UTC de 05/10 é 23:30 de 04/10 em São Paulo (UTC-3)
    d.case(contact, "open", "2026-10-05", hour=2)
    d.login()
    assert d.get(start="2026-10-04", end="2026-10-04")["total"]["cases"] == 1
    assert d.get(start="2026-10-05", end="2026-10-05")["total"]["cases"] == 0


def test_empty_period_has_zeros_and_no_rates(env: Env, client: TestClient) -> None:
    d = Data(env, client)
    d.login()
    report = d.get("product")
    assert report["groups"] == []
    t = report["total"]
    assert t["cases"] == 0 and t["recovered_cents"] == 0
    assert t["recovery_rate"] is None and t["reply_rate"] is None


def test_channels_block_shows_the_conversation_by_channel(env: Env, client: TestClient) -> None:
    d = Data(env, client)
    wa = d.contact("5511930000000")
    d.say(wa, "in", at("2026-09-10", 10))
    d.say(wa, "out", at("2026-09-10", 11))
    social = str(
        env.sql(
            "INSERT INTO contacts (tenant_id, name, channel_only) VALUES (%s, '', true) "
            "RETURNING id",
            (d.tid,),
        )[0][0]
    )
    env.sql(
        "INSERT INTO contact_channels (tenant_id, contact_id, channel, external_id) "
        "VALUES (%s, %s, 'messenger', '2501000000000099')",
        (d.tid, social),
    )
    d.say(social, "in", at("2026-09-10", 12), channel="messenger")
    d.login()
    channels = {c["channel"]: c for c in d.get()["channels"]}
    assert channels["whatsapp"]["inbound"] == 1 and channels["whatsapp"]["outbound"] == 1
    assert channels["messenger"] == {
        "channel": "messenger",
        "conversations": 1,
        "inbound": 1,
        "outbound": 0,
    }


# ------------------------------------------------------------ isolamento e acesso


def test_each_tenant_sees_only_its_own_numbers(rep: Data, env: Env, client: TestClient) -> None:
    before = rep.get()["total"]
    other = Data(env, client)
    c = other.case(other.contact("5511940000000"), "recovered", "2026-09-15", recovered=123456)
    other.step(c, 1, delivery="read", sent=at("2026-09-15", 15))
    other.login()
    mine = other.get()["total"]
    assert (mine["cases"], mine["recovered_cents"]) == (1, 123456)
    rep.login()
    assert rep.get()["total"] == before


def test_agent_can_read_the_report(env: Env, client: TestClient) -> None:
    d = Data(env, client, role="agent")
    d.login()
    assert d.get()["total"]["cases"] == 0


def test_blocked_plan_gets_no_report(rep: Data, env: Env) -> None:
    env.sql("UPDATE tenant_plans SET status = 'canceled' WHERE tenant_id = %s", (rep.tid,))
    res = rep.client.get("/v1/reports/recovery", params={"from": START, "to": END})
    assert res.status_code == 403 and res.json()["error"]["code"] == "plan_required"


def test_report_requires_login(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/v1/reports/recovery").status_code in (401, 403)
    assert client.get("/v1/reports/recovery.csv").status_code in (401, 403)


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"from": "2026-09-30", "to": "2026-09-01"}, "invalid_period"),
        ({"from": "2025-01-01", "to": "2026-09-01"}, "invalid_period"),
        ({"group_by": "cliente"}, "invalid_group"),
    ],
)
def test_invalid_parameters_are_refused(rep: Data, params: dict[str, str], code: str) -> None:
    res = rep.client.get("/v1/reports/recovery", params=params)
    assert res.status_code == 400 and res.json()["error"]["code"] == code
    assert rep.client.get("/v1/reports/recovery", params={"from": "ontem"}).status_code == 422


def test_default_period_is_the_last_30_days_in_the_tenant_timezone(
    env: Env, client: TestClient
) -> None:
    d = Data(env, client)
    d.login()
    report = d.client.get("/v1/reports/recovery").json()
    start = date.fromisoformat(report["from"])
    end = date.fromisoformat(report["to"])
    assert (end - start).days == 29 and report["timezone"] == "America/Sao_Paulo"


# ---------------------------------------------------------------------------- CSV


def test_csv_export_has_the_same_numbers_and_a_download_name(rep: Data) -> None:
    res = rep.client.get(
        "/v1/reports/recovery.csv", params={"from": START, "to": END, "group_by": "source"}
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "recuperacao_2026-09-01_2026-09-30.csv" in res.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(res.text)))
    by_group = {r["grupo"]: r for r in rows}
    assert by_group["checkout"]["casos"] == "4" and by_group["conversa"]["casos"] == "1"
    assert sum(int(r["valor_recuperado_centavos"]) for r in rows) == 14700


def test_csv_neutralizes_spreadsheet_formulas(rep: Data) -> None:
    res = rep.client.get(
        "/v1/reports/recovery.csv", params={"from": START, "to": END, "group_by": "product"}
    )
    rows = list(csv.reader(io.StringIO(res.text)))
    assert len(rows) == 4  # cabeçalho + 3 produtos
    cells = [c for row in rows[1:] for c in row[:1]]
    assert "'" + FORMULA in cells
    assert not any(c.startswith(("=", "+", "-", "@")) for row in rows for c in row)


def test_csv_without_grouping_is_one_total_row(rep: Data) -> None:
    res = rep.client.get("/v1/reports/recovery.csv", params={"from": START, "to": END})
    rows = list(csv.DictReader(io.StringIO(res.text)))
    assert len(rows) == 1 and rows[0]["grupo"] == "total" and rows[0]["casos"] == "5"


@pytest.mark.parametrize("raw", ["=1+1", "+1", "-1", "@SOMA(A1)", "\tcmd", "\rcmd"])
def test_csv_cell_prefixes_formula_starts(raw: str) -> None:
    assert reports.csv_cell(raw) == "'" + raw


@pytest.mark.parametrize("value", ["Curso X", "", 12, None, "1+1", "a=b"])
def test_csv_cell_leaves_normal_values(value: Any) -> None:
    assert reports.csv_cell(value) == value


# -------------------------------------------------------------------- período


def test_parse_period_defaults_and_limits() -> None:
    today = date(2026, 10, 4)
    assert reports.parse_period("America/Sao_Paulo", None, None, today) == (
        date(2026, 9, 5),
        date(2026, 10, 4),
    )
    assert reports.parse_period("UTC", date(2026, 1, 1), date(2026, 1, 1), today) == (
        date(2026, 1, 1),
        date(2026, 1, 1),
    )
    with pytest.raises(AppError):
        reports.parse_period("UTC", date(2026, 10, 5), date(2026, 10, 4), today)
    with pytest.raises(AppError):
        reports.parse_period("UTC", date(2025, 1, 1), date(2026, 10, 4), today)
    assert reports.parse_period("UTC", date(2025, 10, 4), date(2026, 10, 4), today)[0] == date(
        2025, 10, 4
    )
