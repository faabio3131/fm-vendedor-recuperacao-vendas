"""Cakto: payloads no formato da documentação oficial (docs.cakto.com.br, guia de Webhooks).

Os exemplos abaixo reproduzem os campos documentados, com dados sintéticos. Ainda não foram
comparados com um evento real da conta: ver docs/LANCAMENTO_MVP.md.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fm_seller.api.app import create_app
from fm_seller.db import Database
from fm_seller.events.ingest import cakto_signature_ok, dedupe_key, secret_ok
from fm_seller.events.normalize import (
    ABANDONED_CART,
    BOLETO_PENDING,
    PIX_PENDING,
    PURCHASE_APPROVED,
    PURCHASE_REFUSED,
    cakto_dedupe_ref,
    normalize_cakto,
)
from tests.conftest import ORIGIN, Env, login, unique_email
from tests.test_webhook_ingest import Recorder

SECRET = "b3f1a9c2-7b4d-4a8e-9f01-2c6d5b8a4e37"


def _order(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "b3df956e-1998-4322-b091-ac0c54f7b4ba",
        "refId": "4852F91",
        "status": "paid",
        "offer_type": "main",
        "checkoutUrl": "https://pay.cakto.com.br/a8BcHrY?callback=8f31c9e7",
        "baseAmount": 5.0,
        "amount": 5.0,
        "paymentMethod": "credit_card",
        "customer": {
            "id": 481920,
            "name": "John Doe",
            "email": "John.Doe@example.com",
            "phone": "5534999999999",
            "docType": "cpf",
            "docNumber": "12345678909",
        },
        "product": {"id": "cd287b31-d4b7-4e94-858a-96e05ce2f4a2", "name": "Produto Teste"},
        "offer": {"id": "a8BcHrY", "name": "Special Offer", "price": 5.0, "currency": "BRL"},
        "createdAt": "2025-11-06T16:01:53.935243-03:00",
    }
    return base | over


def _envelope(event: str, data: Any) -> dict[str, Any]:
    return {"secret": SECRET, "event": event, "data": data}


ABANDONMENT = _envelope(
    "checkout_abandonment",
    {
        "offer": {"id": "a8BcHrY", "name": "Special Offer", "price": 97.9, "currency": "BRL"},
        "product": {"id": "cd287b31-d4b7-4e94-858a-96e05ce2f4a2", "name": "Produto Teste"},
        "customerName": "Maria Souza",
        "customerEmail": "Maria@Example.com",
        "customerCellphone": "(34) 99999-1234",
        "checkoutUrl": "https://pay.cakto.com.br/a8BcHrY",
        "createdAt": "2025-11-06T16:01:53.935243-03:00",
    },
)


def test_purchase_approved_official_example() -> None:
    ev = normalize_cakto(_envelope("purchase_approved", _order()))
    assert ev is not None
    assert ev.kind == PURCHASE_APPROVED
    assert ev.external_ref == "b3df956e-1998-4322-b091-ac0c54f7b4ba"
    assert ev.email == "john.doe@example.com" and ev.phone == "5534999999999"
    assert ev.product_id == "cd287b31-d4b7-4e94-858a-96e05ce2f4a2"
    assert ev.amount_cents == 500  # 5.0 reais, como no exemplo oficial
    assert ev.payment_url == "https://pay.cakto.com.br/a8BcHrY?callback=8f31c9e7"


def test_checkout_abandonment_uses_its_own_shape() -> None:
    ev = normalize_cakto(ABANDONMENT)
    assert ev is not None and ev.kind == ABANDONED_CART
    assert ev.name == "Maria Souza" and ev.email == "maria@example.com"
    assert ev.phone == "5534999991234"
    assert ev.amount_cents == 9790  # sem `amount` no evento: vale offer.price
    assert ev.payment_url == "https://pay.cakto.com.br/a8BcHrY"
    assert ev.product_id == "cd287b31-d4b7-4e94-858a-96e05ce2f4a2"


def test_pix_refused_and_boleto_events() -> None:
    pix = normalize_cakto(
        _envelope("pix_gerado", _order(status="waiting_payment", pix={"qrCode": "000201..."}))
    )
    assert pix is not None and pix.kind == PIX_PENDING
    assert pix.payment_url == "https://pay.cakto.com.br/a8BcHrY?callback=8f31c9e7"

    refused = normalize_cakto(_envelope("purchase_refused", _order(status="refused")))
    assert refused is not None and refused.kind == PURCHASE_REFUSED

    boleto = normalize_cakto(
        _envelope(
            "boleto_gerado",
            _order(status="waiting_payment", boleto={"boletoUrl": "https://boleto.test/abc"}),
        )
    )
    assert boleto is not None and boleto.kind == BOLETO_PENDING
    assert boleto.payment_url == "https://boleto.test/abc"  # o link do boleto vale mais


def test_webhook_v2_list_uses_the_main_order() -> None:
    bump = _order(id="bump-1", offer_type="orderbump", amount=19.9)
    main = _order(id="main-1", offer_type="main", amount=97.0)
    ev = normalize_cakto(_envelope("purchase_approved", [bump, main]))
    assert ev is not None and ev.external_ref == "main-1" and ev.amount_cents == 9700
    assert normalize_cakto(_envelope("purchase_approved", [])) is not None


def test_events_without_a_recovery_meaning_are_ignored() -> None:
    for event in ("refund_requested", "subscription_paused", "evento_inexistente"):
        assert normalize_cakto(_envelope(event, _order())) is None


def test_dedupe_ref_follows_the_documented_rule() -> None:
    assert cakto_dedupe_ref(_envelope("purchase_approved", _order())) == _order()["id"]
    assert cakto_dedupe_ref(ABANDONMENT) == (
        "maria@example.com|a8BcHrY|2025-11-06T16:01:53.935243-03:00"
    )
    raw = b"{}"
    paid = dedupe_key(_envelope("purchase_approved", _order()), raw, "cakto")
    pix = dedupe_key(_envelope("pix_gerado", _order()), raw, "cakto")
    assert paid != pix  # mesmo pedido, eventos diferentes: ambos precisam ser tratados
    assert paid == dedupe_key(_envelope("purchase_approved", _order()), raw, "cakto")


def _sign(raw: bytes, ts: int, key: str = SECRET) -> dict[str, str]:
    mac = hmac.new(key.encode(), str(ts).encode() + b"." + raw, hashlib.sha256).hexdigest()
    return {"X-Cakto-Timestamp": str(ts), "X-Cakto-Signature": f"v1={mac}"}


def test_signature_valid_wrong_key_tampered_and_expired() -> None:
    raw = json.dumps({"event": "purchase_approved", "data": {}}).encode()
    now = 1_800_000_000
    good = _sign(raw, now)
    assert cakto_signature_ok(good, raw, SECRET, now=now + 10)
    assert not cakto_signature_ok(good, raw + b" ", SECRET, now=now + 10)  # corpo alterado
    assert not cakto_signature_ok(_sign(raw, now, "outra-chave"), raw, SECRET, now=now)
    assert not cakto_signature_ok(good, raw, SECRET, now=now + 301)  # fora dos 5 minutos
    assert not cakto_signature_ok(good, raw, SECRET, now=now - 301)
    assert not cakto_signature_ok({}, raw, SECRET, now=now)
    assert not cakto_signature_ok(good, raw, "", now=now)
    assert not cakto_signature_ok({**good, "X-Cakto-Timestamp": "abc"}, raw, SECRET, now=now)


def test_secret_ok_accepts_signature_or_body_secret_only() -> None:
    raw = json.dumps({"event": "x"}).encode()
    ts = int(time.time())
    # Só a assinatura (sem `secret` no corpo)
    assert secret_ok("cakto", _sign(raw, ts), {"event": "x"}, SECRET, raw)
    # Só o `secret` do corpo
    assert secret_ok("cakto", {}, {"secret": SECRET}, SECRET, raw)
    # Palpites antigos não valem mais: cabeçalhos que a Cakto não documenta
    assert not secret_ok("cakto", {"Authorization": f"Bearer {SECRET}"}, {}, SECRET, raw)
    assert not secret_ok("cakto", {"X-Cakto-Secret": SECRET}, {}, SECRET, raw)
    assert not secret_ok("cakto", {}, {"secret": "errado"}, SECRET, raw)
    assert not secret_ok("cakto", {}, {"secret": SECRET}, "", raw)


@pytest.fixture
def rec() -> Recorder:
    return Recorder()


@pytest.fixture
def wclient(env: Env, db: Database, rec: Recorder) -> Iterator[TestClient]:
    app = create_app(env.settings(), db=db, box=env.box, event_handler=rec)
    with TestClient(app, headers={"Origin": ORIGIN}) as c:
        yield c


def _connect(client: TestClient, env: Env) -> tuple[str, str]:
    email = unique_email("ck")
    tenant_id = env.tenant("Loja Cakto", email)
    assert login(client, email).status_code == 200
    res = client.put("/v1/connections/cakto", json={"values": {"webhook_secret": SECRET}})
    assert res.status_code == 200, res.text
    return tenant_id, res.json()["webhook_url"].rsplit("/", 1)[1]


def test_signed_delivery_end_to_end_without_body_secret(
    wclient: TestClient, env: Env, rec: Recorder
) -> None:
    tenant_id, pid = _connect(wclient, env)
    body = {"event": "purchase_approved", "data": _order()}  # sem `secret`
    raw = json.dumps(body).encode()
    res = wclient.post(
        f"/v1/webhooks/cakto/{pid}",
        content=raw,
        headers={"Content-Type": "application/json", **_sign(raw, int(time.time()))},
    )
    assert res.status_code == 200 and res.json() == {"status": "accepted"}
    assert [e.kind for e in rec.events] == [PURCHASE_APPROVED]
    status = env.sql("SELECT status FROM connections WHERE tenant_id = %s", (tenant_id,))
    assert status[0][0] == "connected"


def test_stale_signature_without_body_secret_is_refused(
    wclient: TestClient, env: Env, rec: Recorder
) -> None:
    _, pid = _connect(wclient, env)
    raw = json.dumps({"event": "purchase_approved", "data": _order()}).encode()
    res = wclient.post(
        f"/v1/webhooks/cakto/{pid}",
        content=raw,
        headers={"Content-Type": "application/json", **_sign(raw, int(time.time()) - 3600)},
    )
    assert res.status_code == 401 and rec.events == []


def test_same_order_two_events_are_both_handled_and_retry_is_deduped(
    wclient: TestClient, env: Env, rec: Recorder
) -> None:
    _, pid = _connect(wclient, env)
    pix = _envelope("pix_gerado", _order(status="waiting_payment"))
    paid = _envelope("purchase_approved", _order())
    assert wclient.post(f"/v1/webhooks/cakto/{pid}", json=pix).json() == {"status": "accepted"}
    assert wclient.post(f"/v1/webhooks/cakto/{pid}", json=paid).json() == {"status": "accepted"}
    assert wclient.post(f"/v1/webhooks/cakto/{pid}", json=paid).json() == {"status": "duplicate"}
    assert [e.kind for e in rec.events] == [PIX_PENDING, PURCHASE_APPROVED]


def test_abandonment_retry_is_deduped_by_email_offer_and_created_at(
    wclient: TestClient, env: Env, rec: Recorder
) -> None:
    _, pid = _connect(wclient, env)
    assert wclient.post(f"/v1/webhooks/cakto/{pid}", json=ABANDONMENT).json() == {
        "status": "accepted"
    }
    assert wclient.post(f"/v1/webhooks/cakto/{pid}", json=ABANDONMENT).json() == {
        "status": "duplicate"
    }
    assert len(rec.events) == 1


def test_test_button_reports_pending_until_an_event_arrives_and_never_downgrades(
    wclient: TestClient, env: Env
) -> None:
    tenant_id, pid = _connect(wclient, env)
    before = wclient.post("/v1/connections/cakto/test").json()
    assert before["status"] == "pending" and "Ainda não chegou" in before["message"]
    assert wclient.post(f"/v1/webhooks/cakto/{pid}", json=ABANDONMENT).status_code == 200
    after = wclient.post("/v1/connections/cakto/test").json()
    assert after["status"] == "connected" and "Confirmada" in after["message"]
    status = env.sql("SELECT status FROM connections WHERE tenant_id = %s", (tenant_id,))
    assert status[0][0] == "connected"
