"""Canonical fulfillment and order snapshot. No LLM-generated amounts or addresses.

Address fields must be explicitly supplied/confirmed; eight-digit CEP only
establishes a range match. It does NOT prove a physical address exists.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from fm_seller.db import Conn
from fm_seller.errors import AppError, bad_request, not_found
from fm_seller.services import audit

MAX_TOTAL_CENTS = 10_000_000
STATES = frozenset(
    {
        "AC",
        "AL",
        "AP",
        "AM",
        "BA",
        "CE",
        "DF",
        "ES",
        "GO",
        "MA",
        "MT",
        "MS",
        "MG",
        "PA",
        "PB",
        "PR",
        "PE",
        "PI",
        "RJ",
        "RN",
        "RS",
        "RO",
        "RR",
        "SC",
        "SP",
        "SE",
        "TO",
    }
)
FIELDS = {
    "postal_code": 8,
    "street": 150,
    "street_number": 25,
    "neighborhood": 100,
    "city": 100,
    "state": 2,
    "complement": 120,
    "reference": 120,
}


def check_address(raw: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, size in FIELDS.items():
        value = raw.get(key, "")
        if not isinstance(value, str) or len(value.strip()) > size:
            raise bad_request("address_field", "Endereço contém dado inválido.")
        result[key] = value.strip()
    result["postal_code"] = result["postal_code"].replace("-", "")
    result["state"] = result["state"].upper()
    if (
        not re.fullmatch(r"[0-9]{8}", result["postal_code"])
        or result["state"] not in STATES
        or any(len(result[key]) < 2 for key in ("street", "neighborhood", "city"))
        or not result["street_number"]
        or not re.fullmatch(r"[0-9A-Za-zÀ-ÿ/., -]{1,25}", result["street_number"])
    ):
        raise bad_request("address_missing", "Confira CEP, rua, número, bairro, cidade e UF.")
    return result


def postal_fee(conn: Conn, tenant: uuid.UUID, postal: str) -> int:
    if not re.fullmatch(r"[0-9]{8}", postal):
        raise bad_request("invalid_postal", "Informe um CEP com oito dígitos.")
    digits = int(postal)
    rows = conn.execute(
        "SELECT fee_cents FROM merchant_delivery_zones WHERE tenant_id=%s "
        "AND active AND cep_start<=%s AND cep_end>=%s FOR SHARE",
        (tenant, digits, digits),
    ).fetchall()
    if len(rows) != 1:
        raise AppError(
            409,
            "delivery_outside_area",
            "Não existe cobertura única para esse CEP. Consulte a loja.",
        )
    return int(rows[0]["fee_cents"])


def quote(
    conn: Conn, tenant: uuid.UUID, method: str, address: dict[str, Any]
) -> tuple[int, dict[str, str]]:
    if method == "pickup":
        return 0, {k: "" for k in FIELDS}
    if method != "delivery":
        raise bad_request("fulfillment_method", "Escolha entrega ou retirada.")
    checked = check_address(address)
    return postal_fee(conn, tenant, checked["postal_code"]), checked


def set_zone(
    conn: Conn,
    tenant: uuid.UUID,
    name: str,
    first: str,
    last: str,
    cents: int,
    *,
    zone_id: uuid.UUID | None = None,
) -> uuid.UUID:
    if (
        not isinstance(name, str)
        or not 2 <= len(name.strip()) <= 100
        or not isinstance(cents, int)
        or isinstance(cents, bool)
        or not 0 <= cents <= 100000
    ):
        raise bad_request("delivery_zone", "Área ou valor inválido.")
    if not (
        re.fullmatch(r"[0-9]{8}", first)
        and re.fullmatch(r"[0-9]{8}", last)
        and int(first) <= int(last)
    ):
        raise bad_request("delivery_postal", "Informe CEP inicial e final válidos.")
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("delivery-zone:" + str(tenant),),
    )
    conflict = conn.execute(
        "SELECT 1 FROM merchant_delivery_zones "
        "WHERE tenant_id=%s AND active AND cep_start<=%s AND cep_end>=%s "
        "AND (%s::uuid IS NULL OR id<>%s::uuid) LIMIT 1",
        (tenant, int(last), int(first), zone_id, zone_id),
    ).fetchone()
    if conflict:
        raise AppError(409, "zone_overlap", "Faixa de CEP sobreposta a outra área ativa.")
    if zone_id:
        result = conn.execute(
            "UPDATE merchant_delivery_zones SET name=%s,cep_start=%s,cep_end=%s,"
            "fee_cents=%s,active=true WHERE tenant_id=%s AND id=%s RETURNING id",
            (name.strip(), int(first), int(last), cents, tenant, zone_id),
        ).fetchone()
        if not result:
            raise not_found("Área de entrega não encontrada.")
        return zone_id
    amount = conn.execute(
        "SELECT count(*) AS n FROM merchant_delivery_zones WHERE tenant_id=%s",
        (tenant,),
    ).fetchone()
    if amount and amount["n"] >= 100:
        raise bad_request("zone_limit", "Limite de 100 áreas por loja.")
    result = conn.execute(
        "INSERT INTO merchant_delivery_zones(tenant_id,name,cep_start,cep_end,fee_cents) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (tenant, name.strip(), int(first), int(last), cents),
    ).fetchone()
    assert result
    return uuid.UUID(str(result["id"]))


def create_order_in_tx(
    conn: Conn,
    *,
    tenant: uuid.UUID,
    actor: uuid.UUID | None,
    key: str,
    name: str,
    phone: str,
    email: str,
    items: list[tuple[uuid.UUID, int]],
    method: str,
    address: dict[str, Any],
) -> uuid.UUID:
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,100}", key):
        raise bad_request("idempotency_key", "Identificador inválido.")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        raise bad_request("customer_name", "Nome inválido.")
    if phone and not re.fullmatch(r"[0-9]{10,15}", phone):
        raise bad_request("customer_phone", "Telefone inválido.")
    if email and (len(email) > 254 or "@" not in email):
        raise bad_request("customer_email", "E-mail inválido.")
    if not 1 <= len(items) <= 20 or len({o for o, _ in items}) != len(items):
        raise bad_request("order_lines", "Itens de pedido inválidos.")
    if any(isinstance(q, bool) or not isinstance(q, int) or not 1 <= q <= 99 for _, q in items):
        raise bad_request("order_quantity", "Quantidade inválida.")

    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("merchant-order:" + str(tenant) + ":" + key,),
    )
    payload = json.dumps(
        {
            "name": name.strip(),
            "phone": phone,
            "email": email,
            "items": sorted((str(oid), q) for oid, q in items),
            "method": method,
            "address": address if method == "delivery" else {},
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
    existing = conn.execute(
        "SELECT id,request_hash FROM merchant_orders WHERE tenant_id=%s AND idempotency_key=%s",
        (tenant, key),
    ).fetchone()
    if existing:
        if existing["request_hash"] != fingerprint:
            raise AppError(
                409, "idempotency_conflict", "Mesma chave utilizada em pedido diferente."
            )
        return uuid.UUID(str(existing["id"]))
    verified = conn.execute(
        "SELECT 1 FROM merchant_accounts WHERE tenant_id=%s AND verified_at IS NOT NULL",
        (tenant,),
    ).fetchone()
    if verified is None:
        raise AppError(409, "merchant_not_ready", "Configure uma conta de recebimento verificada.")
    delivery, checked = quote(conn, tenant, method, address)
    subtotal = 0
    snapshots = []
    for offer_id, qty in items:
        offer = conn.execute(
            "SELECT id,name,price_cents FROM offers "
            "WHERE tenant_id=%s AND id=%s AND active=true FOR SHARE",
            (tenant, offer_id),
        ).fetchone()
        if offer is None or offer["price_cents"] <= 0:
            raise bad_request("offer_invalid", "Produto indisponível ou sem preço.")
        subtotal += offer["price_cents"] * qty
        snapshots.append((offer_id, offer["name"], offer["price_cents"], qty))
    if subtotal <= 0 or subtotal + delivery > MAX_TOTAL_CENTS:
        raise bad_request("order_total", "Total fora dos limites.")
    result = conn.execute(
        "INSERT INTO merchant_orders(tenant_id,idempotency_key,request_hash,"
        "customer_name,customer_phone,customer_email,items_cents,delivery_fee_cents) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (tenant, key, fingerprint, name.strip(), phone, email, subtotal, delivery),
    ).fetchone()
    assert result
    order_id = result["id"]
    for oid, pname, unit, qty in snapshots:
        conn.execute(
            "INSERT INTO merchant_order_items(tenant_id,order_id,offer_id,"
            "product_name,unit_price_cents,quantity) VALUES(%s,%s,%s,%s,%s,%s)",
            (tenant, order_id, oid, pname, unit, qty),
        )
    conn.execute(
        "INSERT INTO merchant_order_fulfillment(tenant_id,order_id,method,"
        "postal_code,street,street_number,neighborhood,city,state,complement,reference)"
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            tenant,
            order_id,
            method,
            checked["postal_code"],
            checked["street"],
            checked["street_number"],
            checked["neighborhood"],
            checked["city"],
            checked["state"],
            checked["complement"],
            checked["reference"],
        ),
    )
    audit(
        conn,
        tenant_id=tenant,
        actor=actor,
        action="merchant.order.created",
        target=str(order_id),
        detail={"total_cents": subtotal + delivery, "method": method},
    )
    return uuid.UUID(str(order_id))
