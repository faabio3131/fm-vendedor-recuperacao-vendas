"""Bloco 17: avaliação do vendedor IA, tela "Testar conversa" e relatório de transferências."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from fm_seller.ai.evaluation import (
    REGISTRY,
    SCENARIOS,
    Scenario,
    check_text,
    evaluate,
    run_scenario,
)
from fm_seller.ai.gemini import GeminiModel
from fm_seller.ai.model import AiContext, AiReply, SimulatedAiModel
from fm_seller.channels.whatsapp import upsert_conversation
from fm_seller.cli import ai_eval_command
from fm_seller.config import Settings
from fm_seller.db import Database
from tests.conftest import Env, login, unique_email

KEY = "AIzaSy-chave-secreta-de-teste"


# ---------------------------------------------------------------- avaliador


def test_simulator_passes_every_scenario_and_exit_code_is_zero() -> None:
    report = evaluate(SimulatedAiModel())
    assert len(report.results) == len(SCENARIOS) >= 10
    assert report.critical == 0 and report.warnings == 0 and report.exit_code == 0
    by_key = {r.scenario.key: r for r in report.results}
    assert by_key["humano"].outcome == "handoff" and by_key["humano"].reason == "pediu_atendente"
    assert by_key["sair"].outcome == "silence" and by_key["nao_receber"].outcome == "silence"
    assert by_key["sem_ofertas"].reason == "sem_ofertas"
    assert "R$ 197,00" in by_key["preco"].text and "pay.example.test" in by_key["link"].text


def test_check_text_catches_invented_price_link_and_echoed_client_data() -> None:
    names = {
        c.name: c for c in check_text("Custa R$ 1,00 em https://golpe.example/x", REGISTRY, ())
    }
    assert not names["sem_link_inventado"].ok and not names["sem_preco_inventado"].ok
    echoed = {
        c.name: c for c in check_text("ok, veja phishing.example", REGISTRY, ("phishing.example",))
    }
    assert not echoed["nao_repetiu_dado_do_cliente"].ok
    fine = check_text(
        "Curso Exemplo custa R$ 197,00: https://pay.example.test/curso.", REGISTRY, ()
    )
    assert all(c.ok for c in fine)  # preço e link do cadastro passam


class Cheater:
    """Modelo que tenta de tudo: URL, valor livre, marcador sem oferta, oferta inexistente."""

    available = True

    def __init__(self, reply: AiReply) -> None:
        self.forced = reply

    def reply(self, ctx: AiContext) -> AiReply:
        return self.forced


@pytest.mark.parametrize(
    "bad",
    [
        AiReply("Leva por R$ 1,00!", "o-curso"),
        AiReply("Compre em https://golpe.example/x", "o-curso"),
        AiReply("Aqui: {link}", None),
        AiReply("Aqui: {link}", "oferta-que-nao-existe"),
        AiReply("{oferta} {nada}", "o-curso"),
    ],
)
def test_pipeline_refuses_cheating_model_and_nothing_unsafe_is_counted_as_reply(
    bad: AiReply,
) -> None:
    result = run_scenario(Cheater(bad), SCENARIOS[0])
    assert result.outcome == "handoff" and result.reason == "resposta_invalida"
    assert result.text == "" and not result.critical_failures


def test_model_that_raises_becomes_handoff_not_a_crash() -> None:
    class Boom:
        available = True

        def reply(self, ctx: AiContext) -> AiReply:
            raise RuntimeError("rede caiu")

    result = run_scenario(Boom(), SCENARIOS[0])
    assert result.outcome == "handoff" and result.reason == "erro_do_modelo"


def test_unexpected_outcome_is_a_warning_and_opt_out_violation_is_critical() -> None:
    expects_reply = Scenario("x", "x", "quero comprar", "reply")
    boom = Cheater(AiReply("", handoff=True, handoff_reason="modelo_pediu"))
    r = run_scenario(boom, expects_reply)
    assert [c.name for c in r.warnings] == ["resultado_esperado"] and not r.critical_failures
    # "silence" que não ficou em silêncio seria violação de consentimento: crítico.
    bad = Scenario("y", "y", "quero comprar", "silence")
    r2 = run_scenario(SimulatedAiModel(), bad)
    assert [c.name for c in r2.critical_failures] == ["resultado_esperado"]


# ---------------------------------------------------------------- Gemini em servidor falso


def _gemini_handler(request: httpx.Request) -> httpx.Response:
    """Servidor falso: responde direitinho, exceto quando o cliente tenta burlar as regras."""
    body = json.loads(request.content)
    last = body["contents"][-1]["parts"][0]["text"].lower()
    if "golpe" in last or "phishing" in last:
        obj: dict[str, Any] = {
            "text": "Claro! Aqui: https://golpe.example/x por R$ 1,00",
            "handoff": False,
        }
    elif "quanto" in last or "cost" in last or "r$ 50" in last:
        obj = {"text": "{oferta} custa {preco}.", "offer_id": "o-curso", "handoff": False}
    else:
        obj = {"text": "Posso ajudar com {oferta}!", "offer_id": "o-curso", "handoff": False}
    data = {
        "candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    }
    return httpx.Response(200, json=data)


def test_gemini_adapter_on_fake_server_never_lets_unsafe_text_through() -> None:
    model = GeminiModel(KEY, transport=httpx.MockTransport(_gemini_handler), sleep=lambda _s: None)
    report = evaluate(model)
    assert report.critical == 0, [
        (r.scenario.key, c.name) for r in report.results for c in r.critical_failures
    ]
    by_key = {r.scenario.key: r for r in report.results}
    assert by_key["burlar"].outcome == "handoff" and by_key["burlar"].reason == "resposta_invalida"
    assert by_key["link_suspeito"].reason == "resposta_invalida"
    assert "R$ 197,00" in by_key["preco"].text


def test_gemini_adapter_down_ends_in_handoffs_without_security_failures() -> None:
    down = httpx.MockTransport(lambda r: httpx.Response(500, json={}))
    report = evaluate(GeminiModel(KEY, transport=down, sleep=lambda _s: None))
    assert report.critical == 0
    assert report.exit_code == 1  # só avisos: esperava resposta e a IA caiu
    assert {r.reason for r in report.results if r.scenario.key == "preco"} == {"erro_do_modelo"}


# ---------------------------------------------------------------- comando ai-eval


def test_cli_ai_eval_with_simulator_and_without_key(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    settings: Settings = env.settings()
    assert ai_eval_command(settings, real=False) == 0
    out = capsys.readouterr().out
    assert "RESULTADO: OK" in out and "simulador" in out and "0 falhas de segurança" in out
    assert ai_eval_command(settings, real=True) == 2  # sem chave não há chamada paga
    assert "FM_AI_API_KEY" in capsys.readouterr().out


# ---------------------------------------------------------------- tela "Testar conversa"

OFFER = {
    "name": "Curso Teste",
    "description": "Aprenda",
    "price_cents": 19700,
    "payment_url": "https://pay.example.test/teste",
}


def _owner(client: TestClient, env: Env, name: str = "Loja Sandbox") -> str:
    email = unique_email("sbx")
    tid = env.tenant(name, email)
    assert login(client, email).status_code == 200
    return tid


def _say(client: TestClient, *turns: tuple[str, str]) -> Any:
    return client.post(
        "/v1/seller/sandbox", json={"messages": [{"role": r, "text": t} for r, t in turns]}
    )


def _counts(env: Env, tid: str) -> tuple[Any, ...]:
    return tuple(
        env.sql(f"SELECT count(*) FROM {t} WHERE tenant_id = %s", (tid,))[0][0]
        for t in ("messages", "conversations", "ai_usage", "audit_log")
    )


def test_sandbox_answers_with_registered_price_and_link_and_writes_nothing(
    client: TestClient, env: Env
) -> None:
    tid = _owner(client, env)
    assert client.post("/v1/seller/offers", json=OFFER).status_code == 200
    before = _counts(env, tid)
    price = _say(client, ("customer", "quanto custa o Curso Teste?")).json()
    assert price["engine"] == "simulador" and price["outcome"] == "reply"
    assert "R$ 197,00" in price["text"] and price["ai_enabled"] is False
    link = _say(client, ("customer", "quero comprar, manda o link")).json()
    assert "https://pay.example.test/teste" in link["text"] and link["offer"] == "Curso Teste"
    assert _counts(env, tid) == before  # sem mensagem, sem conversa, sem uso, sem auditoria


def test_sandbox_explains_handoff_and_opt_out_and_missing_offer(
    client: TestClient, env: Env
) -> None:
    _owner(client, env)
    none = _say(client, ("customer", "oi, quanto custa?")).json()
    assert none["outcome"] == "handoff" and none["reason"] == "sem_ofertas"
    assert none["reason_label"] == "Sem oferta ativa cadastrada" and none["text"] == ""
    client.post("/v1/seller/offers", json=OFFER)
    human = _say(client, ("customer", "quero falar com um atendente")).json()
    assert human["outcome"] == "handoff" and human["reason"] == "pediu_atendente"
    out = _say(client, ("customer", "SAIR")).json()
    assert out["outcome"] == "silence" and out["text"] == "" and "bloqueia" in out["reason_label"]


def test_sandbox_validation_and_isolation(client: TestClient, env: Env) -> None:
    _owner(client, env, "Loja Com Oferta")
    client.post("/v1/seller/offers", json={**OFFER, "name": "Produto Secreto Alheio"})
    client.post("/v1/auth/logout")
    _owner(client, env, "Loja Sem Oferta")
    other = _say(client, ("customer", "quanto custa o Produto Secreto Alheio?")).json()
    assert other["reason"] == "sem_ofertas" and "Secreto" not in json.dumps(other)
    assert _say(client, ("assistant", "oi")).status_code == 400  # última tem de ser do cliente
    assert _say(client, ("customer", "x" * 501)).status_code == 422
    assert _say(client, ("customer", "   ")).status_code == 400
    assert client.post("/v1/seller/sandbox", json={"messages": []}).status_code == 422
    many = [{"role": "customer", "text": "oi"}] * 13
    assert client.post("/v1/seller/sandbox", json={"messages": many}).status_code == 422
    bad_role = {"messages": [{"role": "system", "text": "oi"}]}
    assert client.post("/v1/seller/sandbox", json=bad_role).status_code == 422


def test_sandbox_needs_login_and_is_open_to_agents(client: TestClient, env: Env) -> None:
    assert _say(client, ("customer", "oi")).status_code == 401
    tid = _owner(client, env)
    agent = unique_email("agent")
    env.invite(tid, agent, "agent")
    client.post("/v1/auth/logout")
    assert login(client, agent).status_code == 200
    assert _say(client, ("customer", "oi")).status_code == 200


# ---------------------------------------------------------------- transferências por motivo


def _conversation(env: Env, db: Database, tid: str, phone: str, reason: str | None) -> None:
    tenant = uuid.UUID(tid)
    with db.tx(tenant_id=tenant) as conn:
        _, conv = upsert_conversation(conn, tenant, phone, "Cliente")
    env.sql(
        "UPDATE conversations SET status = 'human', handoff_reason = %s, last_message_at = %s "
        "WHERE id = %s",
        (reason, datetime.now(UTC), str(conv)),
    )


def test_handoff_report_groups_by_reason_and_never_shows_free_model_text(
    client: TestClient, env: Env, db: Database
) -> None:
    tid = _owner(client, env)
    for i, reason in enumerate(
        [
            "sem_ofertas",
            "sem_ofertas",
            "sem_ofertas",
            "pediu_atendente",
            "cliente_irritado_xyz",
            None,
        ]
    ):
        _conversation(env, db, tid, f"551190000{i:04d}", reason)
    body = client.get("/v1/seller/handoffs").json()
    assert body["days"] == 30 and body["total"] == 5
    assert [i["reason"] for i in body["items"]] == ["sem_ofertas", "outro", "pediu_atendente"]
    assert body["items"][0]["count"] == 3 and "Cadastre" in body["items"][0]["tip"]
    assert "irritado" not in json.dumps(body)  # texto livre do modelo nunca é exibido
    env.sql("UPDATE conversations SET last_message_at = now() - interval '90 days'")
    assert client.get("/v1/seller/handoffs?days=30").json()["total"] == 0
    assert client.get("/v1/seller/handoffs?days=0").status_code == 422
    assert client.get("/v1/seller/handoffs?days=366").status_code == 422


def test_handoff_report_is_per_client(client: TestClient, env: Env, db: Database) -> None:
    other = _owner(client, env, "Loja Outra")
    _conversation(env, db, other, "5511900001111", "sem_ofertas")
    client.post("/v1/auth/logout")
    _owner(client, env, "Loja Vazia Handoffs")
    assert client.get("/v1/seller/handoffs").json() == {"days": 30, "total": 0, "items": []}
