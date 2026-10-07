"""Verificação da IA: comando e rota do painel (servidor falso, sem rede nem chave real)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from fm_seller.ai.check import BYPASS_CASE, PRICE_CASE, run_check
from fm_seller.ai.gemini import GeminiModel
from fm_seller.api.app import create_app
from fm_seller.db import Database
from tests.conftest import ORIGIN, Env, login, unique_email
from tests.test_gemini import KEY
from tests.test_platform_admin import admin_client

GOOD = {"text": "Oi! {oferta} custa {preco}.", "offer_id": "oferta-1", "handoff": False}
SCAM = {
    "text": "Claro! Compre em https://golpe.example por R$ 1,00",
    "offer_id": None,
    "handoff": False,
}


def ok_answer(obj: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(obj)}]}, "finishReason": "STOP"}
            ],
            "usageMetadata": {"promptTokenCount": 346, "candidatesTokenCount": 184},
        },
    )


def model_with(*responses: httpx.Response | Exception) -> GeminiModel:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == KEY
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    return GeminiModel(KEY, transport=httpx.MockTransport(handler), retries=0, sleep=lambda _: None)


def test_check_ok_usa_preco_do_cadastro_e_mede_tokens() -> None:
    report = run_check(model_with(ok_answer(GOOD)))
    assert report.ok
    assert [c.name for c in report.cases] == [PRICE_CASE, BYPASS_CASE]
    first = report.cases[0]
    assert first.final_text is not None and "R$ 197,00" in first.final_text
    assert (first.tokens_in, first.tokens_out) == (346, 184)


def test_check_falha_quando_o_google_responde_erro() -> None:
    report = run_check(model_with(httpx.Response(503, json={"error": {"message": "high demand"}})))
    assert not report.ok
    assert all(c.error == "http 503" for c in report.cases)


def test_check_barra_o_link_do_golpe_e_nao_alerta() -> None:
    report = run_check(model_with(ok_answer(SCAM)))
    bypass = next(c for c in report.cases if c.name == BYPASS_CASE)
    # Link ou preço que não vêm do cadastro nunca chegam ao texto final: a resposta é recusada.
    assert bypass.verdict == "RECUSADA"
    assert bypass.final_text is None
    assert bypass.alert is None
    assert report.ok


def client_with_model(env: Env, db: Database, model: GeminiModel | None) -> TestClient:
    app = create_app(env.settings(), db=db, box=env.box, ai_model=model)
    return TestClient(app, headers={"Origin": ORIGIN})


def test_rota_so_para_administrador(env: Env, db: Database) -> None:
    anon = client_with_model(env, db, model_with(ok_answer(GOOD)))
    assert anon.post("/v1/admin/ai-check").status_code == 401
    owner = unique_email("dono")
    env.tenant("Loja sem poder", owner)
    with client_with_model(env, db, model_with(ok_answer(GOOD))) as c:
        assert login(c, owner).status_code == 200
        assert c.post("/v1/admin/ai-check").status_code in (401, 403)


def test_rota_sem_chave_diz_nao_configurada_e_nao_chama_ninguem(env: Env, db: Database) -> None:
    app = create_app(env.settings(), db=db, box=env.box)  # ambiente de teste: simulador, sem chave
    c = TestClient(app, headers={"Origin": ORIGIN})
    c.__enter__()
    try:
        address = unique_email("adm")
        from fm_seller import platform_admin as pa

        assert pa.invite_admin(env.admin_url, address) == "convite"
        assert login(c, address).status_code == 200
        res = c.post("/v1/admin/ai-check")
        assert res.status_code == 200
        assert res.json()["status"] == "not_configured"
    finally:
        c.__exit__(None, None, None)


@pytest.fixture
def admin_with_model(env: Env, db: Database) -> Any:
    from fm_seller import platform_admin as pa

    app = create_app(env.settings(), db=db, box=env.box, ai_model=model_with(ok_answer(GOOD)))
    c = TestClient(app, headers={"Origin": ORIGIN})
    c.__enter__()
    address = unique_email("adm")
    assert pa.invite_admin(env.admin_url, address) == "convite"
    assert login(c, address).status_code == 200
    yield c
    c.__exit__(None, None, None)


def test_rota_devolve_resultado_sem_a_chave_e_limita_a_frequencia(
    admin_with_model: TestClient,
) -> None:
    res = admin_with_model.post("/v1/admin/ai-check")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert [c["name"] for c in body["cases"]] == [PRICE_CASE, BYPASS_CASE]
    assert body["cases"][0]["tokens_in"] == 346
    assert KEY not in res.text  # a chave nunca sai do servidor
    again = admin_with_model.post("/v1/admin/ai-check")
    assert again.status_code == 429  # cada verificação custa: no máximo uma a cada 30 s
    assert "Aguarde" in again.json()["error"]["message"]


def test_admin_client_helper_continua_valido(env: Env, db: Database) -> None:
    c, _ = admin_client(env, db)
    try:
        assert c.post("/v1/admin/ai-check").json()["status"] == "not_configured"
    finally:
        c.__exit__(None, None, None)
