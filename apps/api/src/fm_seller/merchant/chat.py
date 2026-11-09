"""Deterministic conversational cart for already-authenticated merchant WhatsApp threads.

The model does not set prices, delivery fees, checkout links or payment state.
No provider network request is made in the seller's DB transaction.
"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from fm_seller.db import Conn
from fm_seller.errors import AppError
from fm_seller.merchant.fulfillment import STATES, create_order_in_tx, postal_fee
from fm_seller.merchant.service import _only_tax_id
from fm_seller.money import format_brl
from fm_seller.recovery.optout import normalize
from fm_seller.security.crypto import SecretBox

MAX_CART_ITEMS = 8
CANCEL = frozenset({"cancelar pedido", "desistir do pedido", "cancela o pedido"})
YES = frozenset(
    {
        "sim",
        "confirmo",
        "confirmar",
        "confirmado",
        "esta certo",
        "pode confirmar",
        "está certo",
        "pode fechar",
    }
)
NO = frozenset({"nao", "não", "corrigir", "alterar", "mudar", "voltar"})
BUY = frozenset({"quero", "pedir", "pedido", "comprar", "vou levar", "me ve", "manda"})
QWORDS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "três": 3, "quatro": 4, "cinco": 5}
FIELD_PROMPTS = {
    "method": "Você prefere entrega ou retirada no estabelecimento?",
    "postal_code": "Qual é o CEP de entrega? Envie os 8 números.",
    "street": "Qual é a rua ou avenida para a entrega?",
    "street_number": "Qual é o número? Se não tiver, escreva 's/n'.",
    "neighborhood": "Qual é o bairro?",
    "city": "Qual é a cidade?",
    "state": "Qual é a UF (duas letras, por exemplo SP)?",
    "complement": "Tem complemento ou referência? Escreva ou responda 'não'.",
    "name": "Em nome de quem ficará o pedido?",
    "tax_id": "Para gerar a cobrança segura no Asaas, informe o CPF ou CNPJ do pagador. "
    "Não envie dados de cartão; o pagamento será feito no link oficial.",
}


def _matches(text: str, offers: Sequence[Mapping[str, Any]]) -> list[tuple[uuid.UUID, int]]:
    phrase = normalize(text)
    matched: list[tuple[uuid.UUID, int]] = []
    for offer in offers:
        label = normalize(str(offer["name"]))
        if len(label) < 4:
            continue
        hit = re.search(r"(?<![a-z0-9])" + re.escape(label) + r"(?![a-z0-9])", phrase)
        if hit is None:
            continue
        prefix = phrase[max(0, hit.start() - 25) : hit.start()].split()
        qty = 1
        if prefix:
            q = prefix[-1]
            if q.isdecimal():
                qty = int(q)
            elif q in QWORDS:
                qty = QWORDS[q]
        if not 1 <= qty <= 99:
            return []
        matched.append((uuid.UUID(str(offer["id"])), qty))
    return matched if len(matched) <= MAX_CART_ITEMS else []


def _menu(offers: Sequence[Mapping[str, Any]]) -> str:
    shown = [
        f"{o['name']} ({format_brl(o['price_cents'])})" for o in offers if o["price_cents"] > 0
    ][:8]
    return (
        "Informe o nome completo e a quantidade do produto desejado. "
        "Opções cadastradas: " + "; ".join(shown) + "."
    )[:950]


def _write(
    conn: Conn,
    tenant: uuid.UUID,
    cid: uuid.UUID,
    stage: str,
    draft: dict[str, Any],
    items: list[dict[str, Any]],
) -> None:
    conn.execute(
        "UPDATE merchant_conversation_carts SET stage=%s,draft=%s::jsonb,"
        "items=%s::jsonb,expires_at=now()+interval '24 hours',updated_at=now() "
        "WHERE tenant_id=%s AND conversation_id=%s",
        (stage, json.dumps(draft, ensure_ascii=False), json.dumps(items), tenant, cid),
    )


def _quote_snapshot(
    conn: Conn, tenant: uuid.UUID, items: list[dict[str, Any]], draft: dict[str, Any]
) -> tuple[list[tuple[uuid.UUID, int]], int, str]:
    subtotal = 0
    selected = []
    labels = []
    for item in items:
        oid = uuid.UUID(str(item["id"]))
        qty = int(item["qty"])
        if not 1 <= qty <= 99:
            raise ValueError("invalid quantity")
        offer = conn.execute(
            "SELECT name,price_cents FROM offers "
            "WHERE tenant_id=%s AND id=%s AND active=true FOR SHARE",
            (tenant, oid),
        ).fetchone()
        if offer is None or offer["price_cents"] <= 0:
            raise ValueError("offer unavailable")
        subtotal += offer["price_cents"] * qty
        selected.append((oid, qty))
        labels.append(f"{qty}x {offer['name']} ({format_brl(offer['price_cents'] * qty)})")
    method = draft.get("method", "pickup")
    fee = (
        0
        if method == "pickup"
        else postal_fee(
            conn,
            tenant,
            str(draft.get("postal_code", "")),
        )
    )
    return (
        selected,
        subtotal + fee,
        (
            "\n".join(labels)
            + f"\nEntrega: {format_brl(fee)}"
            + f"\nTotal: {format_brl(subtotal + fee)}"
        ),
    )


def handle_cart(
    conn: Conn,
    tenant: uuid.UUID,
    conv_id: uuid.UUID,
    contact_name: str,
    contact_phone: str,
    text: str,
    msg_id: uuid.UUID,
    offers: Sequence[Mapping[str, Any]],
    box: SecretBox | None,
) -> str | None:
    if not text or len(text) > 1000:
        return None
    row = conn.execute(
        "SELECT * FROM merchant_conversation_carts "
        "WHERE tenant_id=%s AND conversation_id=%s FOR UPDATE",
        (tenant, conv_id),
    ).fetchone()
    account = conn.execute(
        "SELECT verified_at FROM merchant_accounts WHERE tenant_id=%s",
        (tenant,),
    ).fetchone()
    if account is None or account["verified_at"] is None:
        if row is None:
            return None
        return "A cobrança está indisponível no momento. Vou encaminhar à equipe."
    phrase = normalize(text).strip(" .!?")
    if row is not None and phrase in CANCEL:
        if row["order_id"] is None:
            conn.execute(
                "DELETE FROM merchant_conversation_carts WHERE tenant_id=%s AND conversation_id=%s",
                (tenant, conv_id),
            )
            return "Pedido cancelado antes da emissão da cobrança."
        return (
            "Seu pedido já está registrado. Para cancelar uma cobrança, "
            "fale com a equipe do estabelecimento."
        )

    if row is not None and row["stage"] == "queued" and (row["expires_at"] < datetime.now(UTC)):
        return (
            "Sua cobrança precisa de conferência da equipe. "
            "Não gere outra cobrança para este pedido."
        )
    fresh_request = (
        row is not None
        and row["stage"] == "finished"
        and any(word in phrase.split() for word in BUY)
        and bool(_matches(text, offers))
    )
    if row is not None and (
        (row["expires_at"] < datetime.now(UTC) and row["stage"] != "queued") or fresh_request
    ):
        conn.execute(
            "DELETE FROM merchant_conversation_carts WHERE tenant_id=%s AND conversation_id=%s",
            (tenant, conv_id),
        )
        row = None

    if row is None:
        if not any(word in phrase.split() for word in BUY):
            return None
        matches = _matches(text, offers)
        if matches:
            items = [{"id": str(oid), "qty": qty} for oid, qty in matches]
            stage = "method"
        else:
            items = []
            stage = "items"
        conn.execute(
            "INSERT INTO merchant_conversation_carts("
            "tenant_id,conversation_id,stage,items,draft,idempotency_key) "
            "VALUES(%s,%s,%s,%s::jsonb,'{}'::jsonb,%s)",
            (tenant, conv_id, stage, json.dumps(items), "cart-" + secrets.token_hex(20)),
        )
        return FIELD_PROMPTS["method"] if matches else _menu(offers)

    stage = str(row["stage"])
    draft = dict(row["draft"])
    items = list(row["items"])
    if stage in ("queued", "finished"):
        if stage == "queued":
            return "Sua cobrança está sendo gerada. Aguarde o link seguro do Asaas."
        if phrase in YES or any(x in phrase for x in ("pago", "pagamento", "status", "link")):
            order = conn.execute(
                "SELECT o.status,c.invoice_url FROM merchant_orders o "
                "LEFT JOIN merchant_charges c ON c.order_id=o.id "
                "AND c.tenant_id=o.tenant_id "
                "WHERE o.tenant_id=%s AND o.id=%s",
                (tenant, row["order_id"]),
            ).fetchone()
            if order and order["status"] == "paid":
                return "O pagamento do pedido foi confirmado pelo provedor."
            if order and order["status"] in ("refunded", "disputed", "review"):
                return "Seu pedido exige conferência financeira. Nossa equipe irá verificar."
            if order and order["invoice_url"]:
                return "Seu pagamento ainda está pendente. Link seguro:\n" + str(
                    order["invoice_url"]
                )
            return "O pedido está em análise. Nossa equipe irá verificar."
        return None

    if stage == "items":
        matched = _matches(text, offers)
        if not matched:
            return _menu(offers)
        items = [{"id": str(oid), "qty": qty} for oid, qty in matched]
        stage = "method"
    elif stage == "method":
        if "retirada" in phrase or "retirar" in phrase or "buscar" in phrase:
            draft["method"] = "pickup"
            stage = "name"
        elif "entrega" in phrase or "delivery" in phrase or "entregar" in phrase:
            draft["method"] = "delivery"
            stage = "postal_code"
        else:
            return FIELD_PROMPTS["method"]
    elif stage == "postal_code":
        if phrase in ("retirada", "retirar", "vou retirar", "buscar"):
            draft = {"method": "pickup"}
            stage = "name"
            _write(conn, tenant, conv_id, stage, draft, items)
            return FIELD_PROMPTS["name"]
        postal = re.sub(r"\D", "", text)
        if len(postal) != 8:
            return FIELD_PROMPTS["postal_code"]
        try:
            fee = postal_fee(conn, tenant, postal)
        except AppError:
            return (
                "Desculpe, o CEP informado não consta em nossa área de entrega. "
                "Informe outro CEP ou escolha retirada."
            )
        draft["postal_code"] = postal
        draft["delivery_fee_cents"] = fee  # informative only, reprice from DB before confirm
        stage = "street"
    elif stage in ("street", "street_number", "neighborhood", "city", "complement", "name"):
        raw = text.strip()
        maxlen = {
            "street": 150,
            "street_number": 25,
            "neighborhood": 100,
            "city": 100,
            "complement": 120,
            "name": 120,
        }[stage]
        if not raw or len(raw) > maxlen:
            return FIELD_PROMPTS[stage]
        if stage == "complement" and phrase in ("nao", "não", "sem", "nenhum", "sem complemento"):
            raw = ""
        if stage == "street_number" and not re.fullmatch(r"[0-9A-Za-zÀ-ÿ/., -]{1,25}", raw):
            return FIELD_PROMPTS[stage]
        if stage in ("street", "neighborhood", "city") and len(raw) < 2:
            return FIELD_PROMPTS[stage]
        if stage == "name" and len(raw) < 2:
            return FIELD_PROMPTS[stage]
        draft[stage] = raw
        stage = {
            "street": "street_number",
            "street_number": "neighborhood",
            "neighborhood": "city",
            "city": "state",
            "complement": "name",
            "name": "confirm",
        }[stage]
    elif stage == "state":
        value = text.strip().upper()
        if value not in STATES:
            return FIELD_PROMPTS["state"]
        draft["state"] = value
        stage = "complement"
    elif stage == "confirm":
        if phrase in NO:
            stage = "method"
            draft = {}
        elif phrase not in YES:
            return "Revise o resumo e responda CONFIRMO ou ALTERAR."
        else:
            try:
                selected, _, _ = _quote_snapshot(conn, tenant, items, draft)
                oid = create_order_in_tx(
                    conn,
                    tenant=tenant,
                    actor=None,
                    key=str(row["idempotency_key"]),
                    name=str(draft["name"]),
                    phone=contact_phone,
                    email="",
                    items=selected,
                    method=str(draft["method"]),
                    address=draft,
                )
            except (AppError, ValueError, KeyError):
                return (
                    "Algum produto, preço, endereço ou frete mudou. "
                    "Peça à equipe para revisar e refazer o pedido."
                )
            conn.execute(
                "UPDATE merchant_conversation_carts SET order_id=%s WHERE "
                "tenant_id=%s AND conversation_id=%s",
                (oid, tenant, conv_id),
            )
            stage = "tax_id"
    elif stage == "tax_id":
        if box is None:
            return "Cobrança indisponível. Nossa equipe deverá concluir o pagamento."
        try:
            cpf = _only_tax_id(text)
        except AppError:
            return "CPF/CNPJ inválido. Confira os números e envie novamente, sem dados de cartão."
        if row["order_id"] is None:
            return "Pedido sem referência. Encaminhe à equipe."
        blob = box.encrypt(
            {"cpf": cpf},
            tenant_id=str(tenant),
            provider="merchant:chat-tax",
        )
        conn.execute(
            "INSERT INTO merchant_chat_charge_jobs("
            "tenant_id,order_id,conversation_id,tax_id_encrypted) "
            "VALUES(%s,%s,%s,%s) ON CONFLICT(tenant_id,order_id) DO NOTHING",
            (tenant, row["order_id"], conv_id, blob),
        )
        # Remove the CPF from durable chat history immediately after encrypting it.
        conn.execute(
            "UPDATE messages SET body='[DOCUMENTO FISCAL PROTEGIDO]' "
            "WHERE tenant_id=%s AND conversation_id=%s AND id=%s",
            (tenant, conv_id, msg_id),
        )
        stage = "queued"
    else:
        return None

    _write(conn, tenant, conv_id, stage, draft, items)
    if stage == "confirm":
        try:
            _, _, summary = _quote_snapshot(conn, tenant, items, draft)
        except (AppError, ValueError, KeyError):
            return "Os itens ou a área de entrega precisam de revisão pela equipe."
        address_line = (
            f"\nEndereço: {draft['street']}, {draft['street_number']} — "
            f"{draft['neighborhood']}, {draft['city']}/{draft['state']} "
            f"CEP {draft['postal_code']}"
            if draft.get("method") == "delivery"
            else "\nRetirada no estabelecimento"
        )
        return (
            "Revise seu pedido:\n" + summary + address_line + f"\nNome: {draft['name']}\n"
            "Está tudo correto? Responda CONFIRMO ou ALTERAR."
        )[:950]
    if stage == "queued":
        return (
            "Pedido registrado! Vou preparar sua cobrança individual no Asaas. "
            "O pedido só será considerado pago após confirmação do provedor."
        )
    return FIELD_PROMPTS[stage]
