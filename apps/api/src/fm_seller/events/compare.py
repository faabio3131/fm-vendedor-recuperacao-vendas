"""Conferência de um evento real contra o que o normalizador espera, campo a campo.

Não usa valores (que podem ser dado pessoal): só caminhos e tipos. Responde, para um evento
capturado: o nome do evento é conhecido? Cada campo que o produto precisa foi achado, em qual
caminho? Que campos o evento traz que ninguém lê? E quais problemas isso causa na recuperação?
"""

from __future__ import annotations

from typing import Any

from fm_seller.events import normalize as n

# Campos sem os quais a recuperação não funciona para aquele tipo de evento.
_NEEDS_CONTACT = (n.ABANDONED_CART, n.PIX_PENDING, n.BOLETO_PENDING, n.PURCHASE_REFUSED)
_NEEDS_URL = (n.ABANDONED_CART, n.PIX_PENDING, n.BOLETO_PENDING)
LABELS = {
    "email": "e-mail do cliente",
    "phone": "telefone do cliente",
    "name": "nome do cliente",
    "product_id": "id do produto",
    "product_name": "nome do produto",
    "ref": "identificador do pedido",
    "amount": "valor",
    "url": "link de pagamento",
}


def _type_name(v: Any) -> str:
    if v is None:
        return "nulo"
    if isinstance(v, bool):
        return "booleano"
    if isinstance(v, int | float):
        return "número"
    if isinstance(v, str):
        return "texto"
    return "lista" if isinstance(v, list) else "objeto"


def leaf_paths(value: Any, prefix: str = "") -> dict[str, str]:
    """Caminho -> tipo de cada folha. Lista vira `[]` no caminho (todos os itens juntos)."""
    out: dict[str, str] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            out.update(leaf_paths(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(value, list) and value:
        for item in value:
            out.update(leaf_paths(item, f"{prefix}[]"))
    else:
        out[prefix] = _type_name(value)
    return out


def _candidates(provider: str, kind: str | None) -> dict[str, tuple[str, ...]]:
    paths = dict(n.PATHS_BY_PROVIDER[provider])
    if provider == "cakto" and kind == n.BOLETO_PENDING:
        paths["url"] = n._CAKTO_BOLETO_URL
    return paths


def compare(provider: str, body: dict[str, Any]) -> dict[str, Any]:
    events = n.EVENTS_BY_PROVIDER[provider]
    event = str(body.get("event", "")).strip()
    kind = events.get(event)
    payload = n.cakto_order(body) if provider == "cakto" else body
    candidates = _candidates(provider, kind)

    fields: list[dict[str, Any]] = []
    used: set[str] = set()
    for key, paths in candidates.items():
        used.update(paths)
        found = next((p for p in paths if n.dig(payload, p) not in (None, "", [], {})), None)
        fields.append(
            {
                "field": key,
                "label": LABELS[key],
                "status": "ok" if found else "ausente",
                "found_at": found,
                "type": None if found is None else _type_name(n.dig(payload, found)),
                "tried": list(paths),
            }
        )
    seen = leaf_paths(payload)
    # Caminho com lista só entra na conta pelo prefixo: `data[].id` cobre `data.id` do Webhook V2.
    covered = {p.replace("[]", "") for p in used}
    extras = [
        {"path": path, "type": typ}
        for path, typ in sorted(seen.items())
        if path.replace("[]", "") not in covered and path not in ("event", "secret", "hottok")
    ]

    by_field = {f["field"]: f for f in fields}
    problems: list[str] = []
    if kind is None:
        problems.append(
            f"Nome de evento desconhecido: '{event or '(vazio)'}'. O produto ignora este evento."
        )
    else:
        if kind in _NEEDS_CONTACT and not (
            by_field["email"]["status"] == "ok" or by_field["phone"]["status"] == "ok"
        ):
            problems.append("Sem e-mail e sem telefone: não dá para falar com o cliente.")
        if kind in _NEEDS_CONTACT and by_field["phone"]["status"] != "ok":
            problems.append("Sem telefone: a recuperação por WhatsApp não envia.")
        if by_field["ref"]["status"] != "ok":
            problems.append("Sem identificador do pedido: o sistema cria um provisório.")
        if by_field["product_id"]["status"] != "ok":
            problems.append("Sem id do produto: compra do SaaS não acha o plano.")
        if by_field["amount"]["status"] != "ok":
            problems.append("Sem valor: o valor recuperado ficaria zerado.")
        if kind in _NEEDS_URL and by_field["url"]["status"] != "ok":
            problems.append("Sem link de pagamento: a mensagem não leva o link.")
    normalized = n.NORMALIZERS[provider](body)
    return {
        "provider": provider,
        "event": event,
        "known_event": kind is not None,
        "kind": kind,
        "normalized": normalized is not None,
        "fields": fields,
        "extras": extras,
        "problems": problems,
    }


def render(report: dict[str, Any]) -> str:
    """Texto para o terminal (CLI)."""
    lines = [
        f"Evento: {report['event'] or '(sem nome)'} · "
        + (f"entendido como {report['kind']}" if report["known_event"] else "DESCONHECIDO"),
        "",
        "Campos que o produto precisa:",
    ]
    for f in report["fields"]:
        where = f"em {f['found_at']} ({f['type']})" if f["found_at"] else "NÃO ACHADO"
        lines.append(f"  [{'ok' if f['status'] == 'ok' else '--'}] {f['label']}: {where}")
        if f["status"] != "ok":
            lines.append(f"       procurado em: {', '.join(f['tried'])}")
    lines += ["", "Campos que o evento traz e ninguém lê:"]
    lines += [f"  {e['path']} ({e['type']})" for e in report["extras"]] or ["  (nenhum)"]
    lines += ["", "Problemas:"]
    lines += [f"  - {p}" for p in report["problems"]] or ["  (nenhum)"]
    return "\n".join(lines)
