"""Primeiros passos do cliente: o que falta para atender e vender, calculado do estado real.

Nada aqui é guardado: cada item sai de uma consulta (conexão testada, oferta ativa, consentimento
declarado...), então a lista nunca fica desatualizada nem "marcada à mão". Só leitura, sempre
dentro da transação do cliente (RLS). O que é pré-requisito de ligar a recuperação ou o vendedor IA
é devolvido em `blocks`, e `missing_for_*` é o que os serviços usam para recusar o "ligar".
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from fm_seller.errors import AppError

SOCIAL_AND_WHATSAPP = ("whatsapp_cloud", "messenger", "instagram_dm")
CHECKOUT = ("cakto", "hotmart")

# O que cada passo impede de ligar enquanto não estiver pronto.
RECOVERY = "recuperacao"
AI = "ia"


class Conn(Protocol):
    def execute(self, query: str, params: Any = ...) -> Any: ...


def _facts(conn: Conn) -> dict[str, Any]:
    """Uma consulta por fato, todas filtradas pela RLS do cliente da transação."""
    connected = {
        r["provider"]
        for r in conn.execute(
            "SELECT provider FROM connections WHERE status = 'connected'"
        ).fetchall()
    }
    offers = conn.execute("SELECT count(*) AS c FROM offers WHERE active").fetchone()
    approved = conn.execute(
        "SELECT count(*) AS c FROM message_templates WHERE meta_status = 'approved'"
    ).fetchone()
    settings = conn.execute(
        "SELECT ai_enabled, ai_persona, recovery_enabled, consent_declared_at FROM tenant_settings"
    ).fetchone()
    assert offers is not None and approved is not None
    s = settings or {}
    return {
        "connected": connected,
        "offers": int(offers["c"]),
        "approved_templates": int(approved["c"]),
        "ai_enabled": bool(s.get("ai_enabled", False)),
        "persona": bool(str(s.get("ai_persona", "")).strip()),
        "recovery_enabled": bool(s.get("recovery_enabled", False)),
        "consent": s.get("consent_declared_at") is not None,
    }


def _step(
    key: str,
    title: str,
    done: bool,
    *,
    why: str,
    todo: str,
    href: str,
    action: str,
    blocks: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "done": done,
        "why": why,
        "todo": "" if done else todo,
        "href": href,
        "action": action,
        "blocks": list(blocks),
    }


def steps(facts: dict[str, Any]) -> list[dict[str, Any]]:
    connected: set[str] = facts["connected"]
    return [
        _step(
            "whatsapp",
            "WhatsApp conectado e testado",
            "whatsapp_cloud" in connected,
            why="É por onde a recuperação envia as mensagens prontas.",
            todo="Conecte o WhatsApp: cadastre os dados do número na central de conexões e teste.",
            href="/connections",
            action="Conectar WhatsApp",
            blocks=(RECOVERY,),
        ),
        _step(
            "ofertas",
            "Ao menos uma oferta ativa",
            facts["offers"] > 0,
            why="Preço e link vêm só das suas ofertas; sem elas a IA não tem o que vender.",
            todo="Cadastre o produto com preço e link de pagamento (https).",
            href="/seller",
            action="Cadastrar oferta",
            blocks=(AI,),
        ),
        _step(
            "consentimento",
            "Consentimento dos contatos declarado",
            facts["consent"],
            why="A recuperação só fala com quem autorizou receber mensagens.",
            todo="Declare, em Recuperação, que seus contatos autorizaram receber mensagens.",
            href="/recovery",
            action="Declarar consentimento",
            blocks=(RECOVERY,),
        ),
        _step(
            "templates",
            "Mensagens aprovadas pela Meta",
            facts["approved_templates"] > 0,
            why="Fora da janela de 24 h, o WhatsApp só entrega mensagem pronta aprovada pela Meta.",
            todo="Envie os textos para aprovação em Recuperação > Mensagens.",
            href="/recovery",
            action="Enviar mensagens para aprovação",
        ),
        _step(
            "persona",
            "Tom de voz do vendedor IA",
            facts["persona"],
            why="Define como o vendedor fala com seus clientes (não muda preço nem regras).",
            todo="Descreva em poucas palavras como sua loja fala.",
            href="/seller",
            action="Definir tom de voz",
        ),
        _step(
            "checkout",
            "Plataforma de vendas conectada (opcional)",
            bool(connected & set(CHECKOUT)),
            why="Cakto ou Hotmart avisam de carrinho abandonado, Pix e boleto para a recuperação.",
            todo="Conecte a plataforma onde você vende, ou registre oportunidades à mão.",
            href="/connections",
            action="Conectar plataforma",
        ),
        _step(
            AI,
            "Vendedor IA ligado",
            facts["ai_enabled"],
            why="Responde as conversas dentro das regras, e passa para uma pessoa quando precisa.",
            todo="Ligue depois de cadastrar uma oferta.",
            href="/seller",
            action="Ligar vendedor IA",
        ),
        _step(
            RECOVERY,
            "Recuperação de vendas ligada",
            facts["recovery_enabled"],
            why="Passa a mandar as mensagens de recuperação para quem não concluiu a compra.",
            todo="Ligue depois de conectar o WhatsApp e declarar o consentimento.",
            href="/recovery",
            action="Ligar recuperação",
        ),
    ]


def _missing(facts: dict[str, Any], target: str) -> list[dict[str, Any]]:
    return [s for s in steps(facts) if target in s["blocks"] and not s["done"]]


def overview(conn: Conn, tenant_id: uuid.UUID) -> dict[str, Any]:
    del tenant_id  # a RLS da transação já restringe tudo ao cliente
    facts = _facts(conn)
    items = steps(facts)
    done = sum(1 for s in items if s["done"])
    can_enable: dict[str, Any] = {}
    for target in (RECOVERY, AI):
        missing = _missing(facts, target)
        can_enable[target] = {"allowed": not missing, "missing": [s["key"] for s in missing]}
    return {
        "steps": items,
        "done": done,
        "total": len(items),
        "percent": round(100 * done / len(items)),
        "can_enable": can_enable,
    }


def _refuse(target: str, missing: list[dict[str, Any]], what: str) -> AppError:
    todo = " ".join(s["todo"] for s in missing)
    return AppError(
        400,
        "setup_incomplete",
        f"Ainda falta preparar antes de ligar {what}: {todo}",
    )


def require_for_recovery(conn: Conn) -> None:
    missing = _missing(_facts(conn), RECOVERY)
    # O consentimento tem mensagem própria (`consent_required`), tratada antes pelo serviço.
    missing = [s for s in missing if s["key"] != "consentimento"]
    if missing:
        raise _refuse(RECOVERY, missing, "a recuperação")


def require_for_ai(conn: Conn) -> None:
    missing = _missing(_facts(conn), AI)
    if missing:
        raise _refuse(AI, missing, "o vendedor IA")
