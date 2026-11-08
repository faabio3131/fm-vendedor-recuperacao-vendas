"""Verified FM product explanations for real pre-sale conversations.

This is NOT a generic answer template for other tenants. Only the registered
FM tenant with its canonical three-plan catalogue may use these statements.
Actual prices and payment URLs remain exclusively in the offers table.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from fm_seller.recovery.optout import normalize

_FM_PLANS = frozenset(
    {"atendevendeia essencial", "atendevendeia profissional", "atendevendeia escala"}
)
_FEATURE_CUES = (
    "o que faz",
    "o que ela faz",
    "o que ele faz",
    "o que o sistema faz",
    "o que a ia faz",
    "faz de verdade",
    "faz na pratica",
    "na pratica",
    "pra que serve",
    "para que serve",
    "como funciona",
    "como e que funciona",
    "qual a funcao",
    "o que oferece",
)
_PAYMENT_UNCERTAINTY = (
    "nao paguei",
    "nao efetuei",
    "nao fiz o pagamento",
    "ainda nao paguei",
    "fiquei com duvida",
    "fiquei com algumas duvidas",
    "tenho duvidas antes",
    "nao finalizei",
)
_PAYMENT_METHODS = {"pix", "boleto", "cartao", "parcelamento", "parcelar"}
_RECOVERY_TERMS = {
    "recuperacao",
    "recuperar",
    "recupera",
    "recuperam",
    "abandono",
    "abandonada",
    "abandonei",
    "retomar",
}


def is_verified_fm_catalog(offers: Sequence[Mapping[str, Any]]) -> bool:
    """Never apply FM-specific capabilities to an unrelated subscriber's catalogue."""
    if len(offers) != 3:
        return False
    return {normalize(str(o.get("name") or "")) for o in offers} == _FM_PLANS


def fm_consultative_reply(
    question: str,
    offers: Sequence[Mapping[str, Any]],
    *,
    tenant_id: uuid.UUID,
    fm_tenant_id: str | None,
    previous_bot: str = "",
) -> str | None:
    """Answer a narrow set of high-confidence doubts without hallucinating a capability.

    Does not infer payment status, invent checkout links, or claim all channels are
    activated. An unrecognized question returns None for normal AI + knowledge.
    """
    if not fm_tenant_id or str(tenant_id) != fm_tenant_id or not is_verified_fm_catalog(offers):
        return None
    phrase = normalize(question)
    words = set(phrase.split())
    if not phrase or len(phrase) > 600:
        return None

    # Reporting is a shipped feature: cite only metrics and navigation confirmed
    # by apps/web/src/app/reports/page.tsx and recovery/reports.py.
    if words & {"relatorio", "relatorios"} and (
        words & {"como", "funciona", "ver", "vejo", "onde", "mostra", "indicadores"}
        or "relatorio de recuperacao" in phrase
    ):
        return (
            "No painel do AtendeVendeIA, abra o menu Relatórios. Ali você escolhe "
            "o período e vê oportunidades de recuperação, mensagens enviadas, "
            "entregues e lidas, clientes que responderam, taxa de recuperação "
            "e valor recuperado quando há compra confirmada e vinculada. "
            "Pode agrupar por dia, produto, sequência ou origem e baixar o CSV. "
            "Uma resposta no WhatsApp não é, sozinha, uma venda paga. "
            "Quer que eu explique como ler a taxa de recuperação?"
        )

    # Give the prospect an example in their own retail segment. No unsupported
    # inventory sync, marketplace integration, checkout creation or Pix status.
    retail_question = (
        "minha empresa" in phrase
        or "minha loja" in phrase
        or "meu negocio" in phrase
        or "o que ela faria" in phrase
        or "como me ajudaria" in phrase
        or "como ajudaria" in phrase
        or "eu vendo" in phrase
        or "trabalho com" in phrase
    )
    if retail_question:
        if words & {"bolsas", "bolsa", "mochila", "mochilas"}:
            return (
                "Na sua loja de bolsas, por exemplo, você cadastraria as ofertas "
                "com marca, modelo, características e link de compra. Se uma cliente "
                "perguntar por uma bolsa transversal de certa marca, a IA pode "
                "explicar os modelos que constam no seu catálogo, esclarecer "
                "as informações cadastradas e encaminhar o link do escolhido. "
                "Se a conversa parar, pode tentar retomá-la pelas regras "
                "de recuperação. Ela não consulta estoque em tempo real nem "
                "sabe que um Pix não foi pago sem integração específica. "
                "Você já tem os modelos organizados em um catálogo?"
            )
        if words & {"roupa", "roupas", "vestidos", "camisetas", "calcados", "sapatos"}:
            return (
                "Na sua loja, você cadastra cada oferta com descrição, variações "
                "que queira informar e link de compra. Quando alguém perguntar "
                "por um item, a IA responde com base nesse catálogo, pode ajudar "
                "a escolher uma opção e enviar o link já cadastrado. "
                "Se a conversa esfriar, pode haver acompanhamento automático "
                "conforme as regras de contato; estoque e pagamento só são "
                "confirmados por integrações próprias. "
                "Você já tem os produtos e links organizados?"
            )

    # Ask about payment mechanics before a generic "como funciona" explanation.
    if words & _RECOVERY_TERMS and (
        words & {"como", "funciona", "fazer", "faz", "vendas", "clientes", "pagamento"}
        or "recuperacao" in words
    ):
        return (
            "A recuperação funciona assim: quando uma conversa esfria ou um link "
            "cadastrado é enviado com confirmação, o AtendeVendeIA pode programar "
            "um acompanhamento pelo WhatsApp, dentro das regras de horário e "
            "autorização do contato. Se o cliente responder, a conversa continua. "
            "Isso não significa que o sistema sabe que um pagamento falhou: "
            "a confirmação financeira depende do provedor do checkout. "
            "Quer que eu explique como você acompanha essas oportunidades no painel?"
        )
    if (
        words & _PAYMENT_METHODS
        or ("como funciona" in phrase and words & {"pagamento", "checkout", "assinatura"})
    ) and words & {"aceita", "tem", "pagamento", "pagar", "funciona", "posso", "como", "receber"}:
        return (
            "O AtendeVendeIA não gera cobranças por conta própria: ele compartilha "
            "o link de checkout cadastrado. As formas de pagamento disponíveis "
            "dependem da configuração do produto na plataforma de cobrança e "
            "aparecem na própria página de pagamento. "
            "Quer que eu explique como conferir isso antes de contratar?"
        )
    # A detailed question takes precedence over the acknowledgement of abandonment.
    if any(cue in phrase for cue in _FEATURE_CUES):
        return (
            "Na prática, imagine alguém chamando sua empresa no WhatsApp: com o canal "
            "conectado, o AtendeVendeIA responde usando as informações e ofertas que "
            "você cadastrou. Se a pessoa quiser contratar, ele apresenta o plano e "
            "envia o link de pagamento configurado. Se ela parar depois de receber "
            "o link, pode fazer um acompanhamento automático, conforme as regras "
            "de contato. Você vê as conversas e os resultados no painel. "
            "Ele não processa pagamentos nem garante vendas; o pagamento acontece "
            "no provedor do checkout. Você quer usar mais para atender clientes "
            "ou para recuperar negociações?"
        )
    if any(cue in phrase for cue in _PAYMENT_UNCERTAINTY):
        resumed = "ficou alguma duvida" in normalize(previous_bot)
        introduction = (
            "Que bom que você voltou. "
            if resumed
            else "Claro, é importante entender antes de decidir. "
        )
        return (
            introduction + "Você pode tirar suas dúvidas sem fazer o pagamento agora. "
            "Quer saber primeiro como a IA atende os clientes, o que muda "
            "entre os planos ou como funciona a contratação pelo link?"
        )
    if "vale a pena" in phrase or "qual a vantagem" in phrase or "beneficio" in words:
        return (
            "O principal benefício é deixar o primeiro atendimento e a "
            "apresentação das suas ofertas mais ágeis, inclusive quando você "
            "não consegue responder na hora. O sistema também ajuda a retomar "
            "conversas que pararam, seguindo as regras de contato. "
            "O resultado depende de como você configura o negócio e as ofertas; "
            "não existe promessa de vendas garantidas. "
            "Sua maior dificuldade hoje é responder rápido ou fazer acompanhamento?"
        )
    # Keep other intents (price, specific guarantees, support, cancellations)
    # with the original guarded model and tenant knowledge.
    return None


def needs_contextual_answer(question: str) -> bool:
    """A copied FAQ paragraph would ignore buying objections and conversational history."""
    phrase = normalize(question)
    return any(cue in phrase for cue in _PAYMENT_UNCERTAINTY) or bool(
        re.search(r"\b(de verdade|na pratica|na real)\b", phrase)
    )
