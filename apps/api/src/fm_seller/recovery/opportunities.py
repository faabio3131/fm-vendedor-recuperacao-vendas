"""Oportunidades de venda registradas pelo lojista: avulsa, planilha (CSV) e desfecho do caso.

A origem não muda o motor: tudo vira um caso de recuperação com os mesmos limites, horários,
consentimento e opt-out. O lojista confirma que o cliente autorizou contato em cada registro.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fm_seller.errors import AppError, bad_request, not_found
from fm_seller.events import normalize as n
from fm_seller.recovery.cases import close_case
from fm_seller.recovery.engine import Opportunity, open_opportunity
from fm_seller.recovery.service import RecoveryService
from fm_seller.services import Principal, audit

MAX_CSV_BYTES = 256 * 1024
MAX_CSV_ROWS = 200
MAX_CENTS = 10_000_000_000  # R$ 100 milhões: acima disso é erro de digitação

# Motivos pelos quais uma oportunidade não virou caso, em português para o painel.
REASONS = {
    "recovery_off": "Ligue a recuperação e declare o consentimento em Ajustes.",
    "no_phone": "Informe um telefone válido com DDD.",
    "suppressed": "Este contato pediu para não receber mensagens.",
    "no_sequence": "A sequência deste tipo está desligada em Ajustes.",
    "duplicate": "Esta oportunidade já estava registrada.",
}

_COLUMNS: dict[str, tuple[str, ...]] = {
    "name": ("nome", "name", "cliente"),
    "phone": ("telefone", "celular", "whatsapp", "fone", "phone"),
    "product": ("produto", "item", "descricao", "product"),
    "amount": ("valor", "preco", "total", "price"),
    "link": ("link", "url", "linkdepagamento"),
    "note": ("observacao", "obs", "nota", "note"),
}


def _slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", plain.lower())


def parse_amount(raw: str) -> int:
    """'R$ 1.234,56', '1234.56', '49,9' → centavos. Levanta ValueError se não for valor."""
    text = re.sub(r"(?i)r\$|\s", "", raw)
    if not text:
        return 0
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError("valor inválido") from None
    cents = int((value * 100).to_integral_value())
    if value < 0 or cents > MAX_CENTS:
        raise ValueError("valor fora do permitido")
    return cents


def _check_link(link: str | None) -> str | None:
    link = (link or "").strip()
    if not link:
        return None
    if not link.startswith("https://") or len(link) > 500 or " " in link:
        raise ValueError("o link precisa começar com https://")
    return link


def _ref_for(phone: str, product: str, cents: int) -> str:
    digest = hashlib.sha256(f"{phone}|{product.casefold()}|{cents}".encode()).hexdigest()
    return f"imp:{digest[:24]}"


def read_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    """Lê o CSV (vírgula, ponto e vírgula ou tab) e devolve (colunas reconhecidas, linhas)."""
    if len(text.encode()) > MAX_CSV_BYTES:
        raise bad_request("csv_too_big", "Arquivo grande demais (máximo 256 KB).")
    text = text.lstrip("﻿")
    head = text.splitlines()[0] if text.strip() else ""
    delim = max(";,\t", key=head.count)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any(c.strip() for c in r)]
    header = [_slug(c) for c in rows[0]]
    index: dict[str, int] = {}
    for field, aliases in _COLUMNS.items():
        for i, col in enumerate(header):
            if col in aliases and field not in index:
                index[field] = i
    if "phone" not in index:
        raise bad_request("csv_no_phone", "A planilha precisa ter uma coluna 'telefone'.")
    data = rows[1:]
    if not data:
        raise bad_request("csv_empty", "A planilha não tem linhas com dados.")
    if len(data) > MAX_CSV_ROWS:
        raise bad_request("csv_too_many", f"No máximo {MAX_CSV_ROWS} linhas por importação.")
    out = [{f: (r[i].strip() if i < len(r) else "") for f, i in index.items()} for r in data]
    return list(index), out


class OpportunityService(RecoveryService):
    def _require_ready(self, p: Principal, authorized: bool) -> None:
        if not authorized:
            raise bad_request(
                "authorization_required",
                "Confirme que o cliente autorizou receber mensagens.",
            )
        if not self.get_settings(p, guard=False)["can_send"]:
            raise bad_request("recovery_off", REASONS["recovery_off"])

    # ---- registro avulso
    def create(
        self,
        p: Principal,
        *,
        name: str,
        phone: str,
        product: str,
        amount_cents: int,
        payment_url: str | None,
        note: str,
        authorized: bool,
    ) -> dict[str, Any]:
        self._guard(p)
        self._require_ready(p, authorized)
        digits = n.normalize_phone_br(phone)
        if not digits:
            raise bad_request("no_phone", REASONS["no_phone"])
        try:
            link = _check_link(payment_url)
        except ValueError as exc:
            raise bad_request("invalid_link", str(exc).capitalize() + ".") from None
        if not 0 <= amount_cents <= MAX_CENTS:
            raise bad_request("invalid_amount", "Valor fora do permitido.")
        opp = Opportunity(
            kind=n.QUOTE_PENDING,
            source="manual",
            external_ref=f"manual:{uuid.uuid4()}",
            name=name.strip(),
            phone=digits,
            product_name=product.strip(),
            amount_cents=amount_cents,
            payment_url=link,
            note=note.strip(),
        )
        with self._tx(p) as conn:
            case_id, reason = open_opportunity(conn, p.tenant_id, opp)
            if case_id is None:
                raise bad_request(reason or "no_sequence", REASONS.get(reason or "", "Não abriu."))
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="recovery.opportunity",
                target=str(case_id),
                detail={"source": "manual"},
            )
        return {"id": str(case_id)}

    # ---- planilha
    def import_csv(self, p: Principal, csv_text: str, authorized: bool) -> dict[str, Any]:
        self._guard(p)
        self._require_ready(p, authorized)
        _, rows = read_csv(csv_text)
        created = 0
        skipped: dict[str, int] = {}
        errors: list[dict[str, Any]] = []
        with self._tx(p) as conn:
            for line, row in enumerate(rows, start=2):  # linha 1 é o cabeçalho
                try:
                    phone = n.normalize_phone_br(row.get("phone", ""))
                    if not phone:
                        raise ValueError(REASONS["no_phone"])
                    cents = parse_amount(row.get("amount", ""))
                    link = _check_link(row.get("link"))
                except ValueError as exc:
                    errors.append({"line": line, "message": str(exc)[:120]})
                    continue
                product = row.get("product", "")[:200]
                opp = Opportunity(
                    kind=n.QUOTE_PENDING,
                    source="importacao",
                    external_ref=_ref_for(phone, product, cents),
                    name=row.get("name", ""),
                    phone=phone,
                    product_name=product,
                    amount_cents=cents,
                    payment_url=link,
                    note=row.get("note", "")[:300],
                )
                case_id, reason = open_opportunity(conn, p.tenant_id, opp)
                if case_id is None:
                    key = reason or "no_sequence"
                    skipped[key] = skipped.get(key, 0) + 1
                else:
                    created += 1
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="recovery.import",
                detail={"rows": len(rows), "created": created},
            )
        return {
            "total": len(rows),
            "created": created,
            "skipped": [
                {"reason": k, "message": REASONS[k], "count": v} for k, v in skipped.items()
            ],
            "errors": errors[:20],
            "errors_total": len(errors),
        }

    # ---- desfecho
    def set_outcome(
        self, p: Principal, case_id: uuid.UUID, outcome: str, amount_cents: int | None
    ) -> dict[str, Any]:
        self._guard(p)
        if outcome not in ("sold", "lost"):
            raise bad_request("invalid_outcome", "Desfecho inválido.")
        if amount_cents is not None and not 0 <= amount_cents <= MAX_CENTS:
            raise bad_request("invalid_amount", "Valor fora do permitido.")
        now = datetime.now(UTC)
        with self._tx(p) as conn:
            case = conn.execute(
                "SELECT status, amount_cents FROM recovery_cases WHERE id = %s FOR UPDATE",
                (case_id,),
            ).fetchone()
            if case is None:
                raise not_found("Caso não encontrado.")
            if case["status"] not in ("open", "exhausted"):
                raise AppError(409, "case_closed", "Este caso já foi encerrado.")
            if outcome == "lost":
                status, reason, recovered = "stopped", "perdido", None
            else:
                sent = conn.execute(
                    "SELECT count(*) AS c FROM recovery_steps WHERE case_id = %s "
                    "AND status IN ('sent', 'sending')",
                    (case_id,),
                ).fetchone()
                assert sent is not None
                value = case["amount_cents"] if amount_cents is None else amount_cents
                status = "recovered" if sent["c"] > 0 else "purchased"
                reason, recovered = "vendido", value if sent["c"] > 0 else None
            close_case(conn, case_id, status, reason, now, recovered)
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="recovery.outcome",
                target=str(case_id),
                detail={"outcome": outcome},
            )
        return {"id": str(case_id), "status": status, "recovered_cents": recovered}
