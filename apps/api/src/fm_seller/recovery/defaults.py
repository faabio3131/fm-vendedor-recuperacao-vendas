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
    n.QUOTE_PENDING: [
        {"delay_minutes": 24 * 60, "template_key": "orcamento_1"},
        {"delay_minutes": 3 * 24 * 60, "template_key": "orcamento_2"},
        {"delay_minutes": 7 * 24 * 60, "template_key": "orcamento_3"},
    ],
    n.CONVERSATION_COLD: [
        {"delay_minutes": 1, "template_key": "conversa_1"},
        {"delay_minutes": 2 * 24 * 60, "template_key": "conversa_2"},
    ],
}

# Nenhum texto começa nem termina com variável: há relatos de que a Meta reprova assim (não
# confirmado na documentação). O teste `test_default_templates_*` garante isso.
DEFAULT_TEMPLATES: dict[str, str] = {
    "carrinho_1": "Oi, {nome}! Vi que você começou a comprar {produto} e não finalizou. "
    "Se quiser retomar, o link é {link}. Qualquer dúvida, é só responder esta mensagem.",
    "carrinho_2": "Oi, {nome}! Sua compra de {produto} ainda está esperando por você. "
    "Link para finalizar: {link}. Se ficou alguma dúvida, é só responder esta mensagem.",
    "carrinho_3": "Oi, {nome}! Última mensagem sobre {produto}. "
    "Se ainda fizer sentido para você, o link é {link}. Qualquer dúvida, estou por aqui.",
    "pix_1": "Oi, {nome}! Seu PIX de {produto} ({valor}) foi gerado e ainda não foi pago. "
    "Se precisar, o link é {link}. Qualquer dúvida, é só responder esta mensagem.",
    "pix_2": "Oi, {nome}! O PIX de {produto} ainda está em aberto. Link: {link}. "
    "Posso ajudar com algo? É só responder esta mensagem.",
    "boleto_1": "Oi, {nome}! O boleto de {produto} ({valor}) está em aberto. Link: {link}. "
    "Qualquer dúvida, é só responder esta mensagem.",
    "boleto_2": "Oi, {nome}! Lembrete: o boleto de {produto} ainda não foi pago. Link: {link}. "
    "Se precisar de ajuda, é só responder esta mensagem.",
    "recusada_1": "Oi, {nome}! O pagamento de {produto} não foi aprovado. "
    "Quer tentar de novo ou usar outra forma de pagamento? O link é {link}. "
    "Estou por aqui para ajudar.",
    "recusada_2": "Oi, {nome}! Ainda dá para concluir a compra de {produto}. Link: {link}. "
    "Posso ajudar? É só responder esta mensagem.",
    "orcamento_1": "Oi, {nome}! Passando para saber se você conseguiu avaliar o orçamento de "
    "{produto} ({valor}). O link é {link}. Posso ajudar com alguma dúvida?",
    "orcamento_2": "Oi, {nome}! O orçamento de {produto} ainda está disponível. Link: {link}. "
    "Se quiser ajustar algo, é só responder esta mensagem.",
    "orcamento_3": "Oi, {nome}! Última mensagem sobre o orçamento de {produto}. "
    "Se ainda fizer sentido para você, o link é {link}. Estou por aqui.",
    "conversa_1": "Oi, {nome}! Ficou alguma dúvida sobre o que conversamos? "
    "Se quiser, continuamos de onde paramos.",
    "conversa_2": "Oi, {nome}! Sigo à disposição se ainda tiver interesse. "
    "É só responder esta mensagem.",
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
