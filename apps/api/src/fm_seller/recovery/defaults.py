"""Sequências e textos padrão da plataforma, em português do Brasil.

Os textos são PONTO DE PARTIDA: no WhatsApp, mensagem fora da janela de 24 h só pode ir como
template aprovado pela Meta. Por isso nada daqui é enviado enquanto o cliente não tiver o template
com a mesma chave marcado como `approved` (ver `message_templates`).

Variáveis aceitas nos textos: {nome} {produto} {valor} {link}.
"""

from __future__ import annotations

from typing import Any

from fm_seller.events import normalize as n

# delay_minutes é contado a partir da abertura do caso (não do passo anterior).
DEFAULT_SEQUENCES: dict[str, list[dict[str, Any]]] = {
    n.ABANDONED_CART: [
        {"delay_minutes": 30, "template_key": "carrinho_1"},
        {"delay_minutes": 24 * 60, "template_key": "carrinho_2"},
        {"delay_minutes": 3 * 24 * 60, "template_key": "carrinho_3"},
    ],
    n.PIX_PENDING: [
        {"delay_minutes": 15, "template_key": "pix_1"},
        {"delay_minutes": 3 * 60, "template_key": "pix_2"},
    ],
    n.BOLETO_PENDING: [
        {"delay_minutes": 24 * 60, "template_key": "boleto_1"},
        {"delay_minutes": 3 * 24 * 60, "template_key": "boleto_2"},
    ],
    n.PURCHASE_REFUSED: [
        {"delay_minutes": 10, "template_key": "recusada_1"},
        {"delay_minutes": 24 * 60, "template_key": "recusada_2"},
    ],
}

DEFAULT_TEMPLATES: dict[str, str] = {
    "carrinho_1": "Oi, {nome}! Vi que você começou a comprar {produto} e não finalizou. "
    "Posso ajudar com alguma dúvida? Se quiser retomar: {link}",
    "carrinho_2": "{nome}, sua compra de {produto} ainda está esperando por você. "
    "Se ficou alguma dúvida, é só responder esta mensagem. Link: {link}",
    "carrinho_3": "Última mensagem sobre {produto}, {nome}. "
    "Se ainda fizer sentido para você: {link}",
    "pix_1": "Oi, {nome}! Seu PIX de {produto} ({valor}) foi gerado e ainda não "
    "foi pago. Se precisar, o link é: {link}",
    "pix_2": "{nome}, o PIX de {produto} ainda está em aberto. Posso ajudar com algo? {link}",
    "boleto_1": "Oi, {nome}! O boleto de {produto} ({valor}) está em aberto. Link: {link}",
    "boleto_2": "{nome}, lembrete: o boleto de {produto} ainda não foi pago. {link}",
    "recusada_1": "Oi, {nome}! O pagamento de {produto} não foi aprovado. "
    "Quer tentar de novo ou usar outra forma de pagamento? {link}",
    "recusada_2": "{nome}, ainda dá para concluir a compra de {produto}. Posso ajudar? {link}",
}


def default_steps(trigger_kind: str) -> list[dict[str, Any]]:
    return [dict(s) for s in DEFAULT_SEQUENCES.get(trigger_kind, [])]


def validate_steps(steps: object) -> list[dict[str, Any]]:
    """Valida uma sequência vinda do cliente. Levanta ValueError com mensagem em português."""
    if not isinstance(steps, list) or not 1 <= len(steps) <= 10:
        raise ValueError("A sequência precisa ter de 1 a 10 passos.")
    out: list[dict[str, Any]] = []
    last = -1
    for item in steps:
        if not isinstance(item, dict):
            raise ValueError("Passo inválido.")
        delay = item.get("delay_minutes")
        key = item.get("template_key")
        if not isinstance(delay, int) or isinstance(delay, bool) or not 1 <= delay <= 30 * 1440:
            raise ValueError("O intervalo de cada passo deve ficar entre 1 minuto e 30 dias.")
        if delay <= last:
            raise ValueError("Os passos precisam estar em ordem crescente de tempo.")
        if not isinstance(key, str) or not key or len(key) > 60:
            raise ValueError("Cada passo precisa de uma chave de template.")
        last = delay
        out.append({"delay_minutes": delay, "template_key": key})
    return out
