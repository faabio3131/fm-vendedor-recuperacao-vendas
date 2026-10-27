"""Vendedor IA: responde quem escreveu, dentro de limites que o modelo não consegue contornar.

Regras:
- preço, nome da oferta e link de pagamento só entram a partir do cadastro (placeholders);
  texto do modelo com URL ou valor em reais é recusado e a conversa vai para uma pessoa;
- pedido explícito de falar com pessoa, conversa sem ofertas ativas, IA desligada/indisponível
  ou excesso de respostas por hora transferem para uma pessoa;
- quem pediu para não ser contatado nunca recebe resposta;
- a resposta é enfileirada na mesma transação que marca a mensagem como tratada: no máximo uma.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fm_seller.ai import usage
from fm_seller.ai.abbreviations import expand_abbreviations
from fm_seller.ai.model import AiContext, AiModel, AiReply, OfferView
from fm_seller.channels.outbound import HANDOFF_MESSAGE, enqueue_text
from fm_seller.channels.pilot_greeting import (
    DEMO_PAGE,
    DEMO_VOLUME_QUESTION,
    demo_plan_listing_requested,
    demo_recommendation_requested,
    demo_requested_volume,
    greeting_only,
    pilot_contact,
)
from fm_seller.channels.social import identity_of
from fm_seller.config import Settings
from fm_seller.db import Conn, Database
from fm_seller.money import format_brl
from fm_seller.provisioning import trial
from fm_seller.provisioning.lifecycle import blocks_service
from fm_seller.recovery.optout import normalize

log = logging.getLogger("fm_seller.seller")
MAX_BOT_REPLIES_PER_HOUR = 10
# Only the single tenant/contact authorized for the FM pilot gets a longer consultative
# exchange. General SaaS tenants retain the original strict anti-abuse ceiling.
MAX_PILOT_DEMO_REPLIES_PER_HOUR = 40
HISTORY = 10
_HUMAN = ("atendente", "humano", "pessoa", "falar com alguem", "gerente", "responsavel")
MODEL_RETRY_MESSAGE = "Só um instante, já te respondo."
_FORBIDDEN = re.compile(r"https?://|www\.|r\$|\breais\b|\bpix\b.*\bchave\b", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\{(oferta|preco|link)\}")


@dataclass
class SellerStats:
    conversations: int = 0
    replied: int = 0
    handoffs: int = 0
    ignored: int = 0


def asks_for_human(text: str) -> bool:
    norm = normalize(text)
    return any(k in norm for k in _HUMAN)


_STOP = frozenset(
    [
        "como",
        "qual",
        "quais",
        "para",
        "pelo",
        "pela",
        "pelos",
        "pelas",
        "isso",
        "essa",
        "esse",
        "esta",
        "este",
        "aqui",
        "sobre",
        "quero",
        "preciso",
        "gostaria",
        "saber",
        "posso",
        "pode",
        "podem",
        "fazer",
        "voces",
        "voce",
        "seu",
        "sua",
        "meu",
        "minha",
        "tem",
        "tenho",
        "ter",
        "uma",
        "uns",
        "umas",
        "dos",
        "das",
        "nos",
        "nas",
        "que",
        "com",
        "por",
        "mais",
        "muito",
        "ola",
        "bom",
        "dia",
        "tarde",
        "noite",
    ]
)


# Como o cliente se refere ao produto ("o que ela faz?", "o que o sistema faz?"): todas as
# formas contam como a mesma palavra de conteúdo, senão a pergunta cadastrada não casa.
_PRODUCT_ALIASES = frozenset(
    ["ela", "ele", "atendevendeia", "anaclara", "sistema", "ferramenta", "plataforma", "produto"]
)
_SHORT_CONTENT = frozenset(["faz"])


def _content_words(text: str) -> set[str]:
    words: set[str] = set()
    for w in normalize(text).split():
        if w in _PRODUCT_ALIASES:
            words.add("produto")
        elif (len(w) >= 4 or w in _SHORT_CONTENT) and w not in _STOP:
            words.add(w)
    return words


DEMO_MESSAGES_CLARIFICATION = (
    "Entendi. A franquia dos planos conta respostas de IA, não "
    "mensagens recebidas. Aproximadamente quantas dessas mensagens "
    "você gostaria que a IA respondesse por mês?"
)
# "Todas", "todas elas", "sim"...: o cliente confirma que quer a IA respondendo o volume que
# ele mesmo já informou antes.
_ALL_OF_THEM = frozenset(
    [
        "todas",
        "todos",
        "tudo",
        "todas elas",
        "todos eles",
        "todas essas",
        "todas as mensagens",
        "sim",
        "sim todas",
        "isso",
        "isso mesmo",
        "exatamente",
        "pode ser",
    ]
)


def asked_for_volume(previous_bot: str) -> bool:
    """A mensagem anterior do bot terminou pedindo o volume mensal de respostas de IA.

    O modelo também faz essa pergunta (com outras palavras) ao explicar os planos; um "1500"
    em seguida é a resposta, não uma conversa nova.
    """
    if previous_bot in (DEMO_VOLUME_QUESTION, DEMO_MESSAGES_CLARIFICATION):
        return True
    tail = normalize(previous_bot)[-90:]
    return bool(re.search(r"quantas respostas de ia .{0,40}por mes$", tail))


def knowledge_reply(question: str, items: Sequence[tuple[str, str]]) -> str | None:
    """Resposta sem modelo quando a pergunta do cliente é praticamente uma pergunta cadastrada.

    Exige que ao menos 2 palavras de conteúdo coincidam e que a maior parte das palavras da
    pergunta cadastrada apareça na do cliente. Se houver dúvida ou empate, devolve None e o
    modelo (que também recebe a base) decide. O texto vem do cadastro do cliente.
    """
    asked = _content_words(question)
    if len(asked) < 2:
        return None
    scored: list[tuple[float, int, str]] = []
    for stored_q, answer in items:
        words = _content_words(stored_q)
        shared = len(words & asked)
        if len(words) < 2 or shared < 2 or not answer.strip():
            continue
        coverage = shared / len(words)
        if coverage >= 0.7:
            scored.append((coverage, shared, answer.strip()))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][:2] == scored[1][:2] and scored[0][2] != scored[1][2]:
        return None  # empate entre respostas diferentes: deixa o modelo decidir
    return scored[0][2] if len(scored[0][2]) <= 700 else None


def render_reply(reply: AiReply, offers: dict[str, tuple[str, str, str]]) -> str | None:
    """Preenche os placeholders com dados do cadastro. None = resposta inválida (recusada)."""
    raw = reply.text.strip()
    if not raw or len(raw) > 1000 or _FORBIDDEN.search(raw):
        return None
    if "{" in _PLACEHOLDER.sub("", raw) or "}" in _PLACEHOLDER.sub("", raw):
        return None  # marcador desconhecido ou chave solta: nunca vai para o cliente
    if _PLACEHOLDER.search(raw):
        if reply.offer_id is None or reply.offer_id not in offers:
            return None
        name, price, link = offers[reply.offer_id]
        values = {"oferta": name, "preco": price, "link": link}
        raw = _PLACEHOLDER.sub(lambda m: values[m.group(1)], raw)
    return raw


def catalog_offer_reply(
    question: str, offer_rows: Sequence[Mapping[str, Any]], *, demo: bool
) -> str | None:
    """Zero-model answer only for one unambiguous named, registered offer.

    The customer's text selects intent, never a price or URL. Ambiguous, unsupported
    or invalid catalogue data falls through to the governed model.
    """
    phrase = normalize(question)
    terms = set(phrase.split())
    price = bool(terms & {"preco", "precos", "valor", "valores", "custa", "custam"})
    details = bool(terms & {"link", "pagina", "site"})
    if not (price or details):
        return None
    matched = [
        offer
        for offer in offer_rows
        if isinstance(offer["name"], str)
        and len(name := normalize(offer["name"])) >= 4
        and re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", phrase)
    ]
    if len(matched) != 1:
        return None
    offer = matched[0]
    name, cents, url = offer["name"], offer["price_cents"], offer["payment_url"]
    if not isinstance(cents, int) or isinstance(cents, bool) or cents < 0:
        return None
    if price:
        answer = (
            f"{name} custa {format_brl(cents)} por mês. Posso explicar os detalhes."
            if demo
            else f"{name} custa {format_brl(cents)}. Quer que eu envie o link?"
        )
    else:
        if not isinstance(url, str) or not url.startswith("https://"):
            return None
        answer = (
            f"Detalhes e demonstração de {name}: {url}"
            if demo
            else f"Aqui está o link cadastrado para {name}: {url}"
        )
    return answer if len(answer) <= (850 if demo else 1000) else None


def _demo_recommendation_line(offer: Mapping[str, Any]) -> str | None:
    """Reconstruct the exact recommendation using only the approved plan row."""
    quota = str(offer.get("quota") or "")
    name, cents = offer.get("name"), offer.get("price_cents")
    if (
        not quota.isdigit()
        or int(quota) <= 0
        or not isinstance(name, str)
        or not name.strip()
        or not isinstance(cents, int)
        or isinstance(cents, bool)
        or cents <= 0
    ):
        return None
    formatted_quota = f"{int(quota):,}".replace(",", ".")
    return (
        f"Pelo volume informado, recomendo {name}: {format_brl(cents)}/mês, "
        f"com até {formatted_quota} respostas de IA mensais. "
        "Quer conhecer os detalhes ou já posso te enviar o link de pagamento?"
    )


def demo_plan_details_reply(
    question: str, previous_bot: str, offer_rows: Sequence[Mapping[str, Any]]
) -> str | None:
    """Continue the *same* approved commercial conversation without model speculation.

    A short "Quero" is not a new purchase instruction; it is contextual only
    after our exact plan recommendation or catalogue price explanation.
    """
    phrase = normalize(question).strip("?!., ")
    affirmative = phrase in {
        "quero",
        "sim",
        "sim quero",
        "quero sim",
        "quero conhecer",
        "quero entender",
        "entender",
        "sim quero conhecer",
        "quero saber mais",
        "sim quero saber mais",
        "pode explicar",
        "me explica",
        "me explique",
        "pode",
        "claro",
        "gostaria",
    }
    terms = set(phrase.split())
    informational = bool(
        terms
        & {
            "funciona",
            "funcionalidades",
            "recursos",
            "beneficios",
            "detalhes",
            "explicar",
            "faz",
            "inclui",
            "franquia",
            "consumo",
            "limite",
            "limites",
            "contratar",
            "contratacao",
            "contabiliza",
            "contabilizado",
        }
    )
    if not affirmative and not informational:
        return None
    matching_previous: list[Mapping[str, Any]] = []
    for offer in offer_rows:
        recommended = _demo_recommendation_line(offer)
        cents, name = offer.get("price_cents"), offer.get("name")
        earlier_price = (
            f"{name} custa {format_brl(cents)} por mês. Posso explicar os detalhes."
            if isinstance(name, str) and isinstance(cents, int) and not isinstance(cents, bool)
            else None
        )
        quota = str(offer.get("quota") or "")
        if recommended and (
            previous_bot in (recommended, earlier_price)
            or (
                quota.isdigit()
                and previous_bot
                in {
                    _demo_detail_text(offer, kind)
                    for kind in ("overview", "features", "usage", "limits", "contract")
                }
            )
        ):
            matching_previous.append(offer)
    named = [
        offer
        for offer in offer_rows
        if isinstance(offer.get("name"), str)
        and re.search(rf"(?<![a-z0-9]){re.escape(normalize(offer['name']))}(?![a-z0-9])", phrase)
    ]
    if affirmative:
        if len(matching_previous) != 1:
            return None
        selected = matching_previous[0]
    elif len(named) == 1:
        selected = named[0]
    elif len(matching_previous) == 1:
        selected = matching_previous[0]
    else:
        return None
    if not _demo_recommendation_line(selected):
        return None
    category = "overview"
    if "nao" in terms and terms & {"faz", "inclui", "oferece", "permite"}:
        category = "limits"
    elif terms & {"consumo", "franquia", "contabiliza", "contabilizado", "limite", "limites"}:
        category = "usage"
    elif terms & {"contratar", "contratacao"}:
        category = "contract"
    elif "entender" in terms and not terms & {"faz", "inclui", "funcionalidades", "recursos"}:
        category = "usage"
    elif terms & {"faz", "inclui", "funcionalidades", "recursos", "funciona", "beneficios"}:
        category = "features"
    return _demo_detail_text(selected, category)


def _demo_detail_text(offer: Mapping[str, Any], category: str) -> str:
    """Only claims supported by the existing product modules and catalogue."""
    name = str(offer["name"])
    quota = f"{int(offer['quota']):,}".replace(",", ".")
    price = format_brl(offer["price_cents"])
    if category == "limits":
        return (
            f"O {name} não oferece respostas de IA ilimitadas: a franquia é de "
            f"{quota} por mês, compartilhada entre canais. "
            "Integrações dependem da configuração das conexões. "
            "O pagamento é feito por um link seguro e a equipe confirma a ativação da "
            f"sua conta. {_checkout_cta(name)}"
        )
    if category == "usage":
        return (
            f"No {name}, a franquia é de {quota} respostas de IA por mês, "
            "compartilhada entre canais. Não é ilimitada e os três planos "
            f"oferecem as mesmas funcionalidades. O valor é {price}/mês. "
            f"{_checkout_cta(name)}"
        )
    if category == "contract":
        return (
            f"O {name} custa {price}/mês para {quota} respostas de IA mensais. "
            f"{_checkout_cta(name)}"
        )
    if category == "features":
        return (
            "O AtendeVendeIA reúne Vendedor IA para responder clientes, "
            "painel de Conversas e recursos de Recuperação e Relatórios, "
            "conforme as conexões configuradas. Os três planos têm as mesmas "
            f"funcionalidades; no {name}, são {quota} respostas de IA/mês, "
            f"por {price}/mês. Quer entender a franquia ou já posso te enviar o "
            "link de pagamento?"
        )
    return (
        f"Claro! O {name} oferece Vendedor IA para atendimento, painel de "
        "Conversas e recursos de Recuperação e Relatórios, conforme as conexões "
        f"configuradas. Inclui {quota} respostas de IA por mês, compartilhadas "
        f"entre canais, por {price}/mês. Não são respostas ilimitadas. "
        "Para contratar, o pagamento é feito por um link seguro e a equipe confirma a "
        f"ativação da sua conta. Detalhes: {DEMO_PAGE}. O que gostaria de saber primeiro?"
    )


DEMO_SAFE_FALLBACK = (
    "Posso te ajudar a escolher o plano e já te envio o link de pagamento. "
    "Quantas respostas de IA você espera usar por mês, ou qual plano prefere: "
    "Essencial, Profissional ou Escala?"
)


def _checkout_cta(name: str) -> str:
    return f"Posso te enviar o link de pagamento do {name}?"


def _demo_checkout_text(offer: Mapping[str, Any]) -> str | None:
    """Fechamento: plano, preço e o link de pagamento cadastrado para ele (nunca do modelo)."""
    url = offer.get("checkout_url")
    quota = str(offer.get("quota") or "")
    name, cents = offer.get("name"), offer.get("price_cents")
    if (
        not isinstance(url, str)
        or not url.startswith("https://")
        or not quota.isdigit()
        or not isinstance(name, str)
        or not isinstance(cents, int)
        or isinstance(cents, bool)
    ):
        return None
    formatted_quota = f"{int(quota):,}".replace(",", ".")
    return (
        f"Perfeito! O {name} custa {format_brl(cents)}/mês, com {formatted_quota} respostas "
        f"de IA por mês. Para contratar, faça o pagamento neste link seguro:\n{url}\n\n"
        "Quando o pagamento for confirmado, a equipe da FM Tecnologia IA ativa a sua conta. "
        "Se preferir outro plano, é só me dizer."
    )


_BUY_VERBS = frozenset(
    ["contratar", "assinar", "fechar", "comprar", "adquirir", "pagar", "contrato", "assino", "pago"]
)
_BUY_SUBJECTS = frozenset(
    ["quero", "vou", "gostaria", "queria", "desejo", "preciso", "posso", "como", "bora", "topo"]
)
_SEND_VERBS = frozenset(["manda", "mande", "mandar", "envia", "envie", "enviar", "passa", "passe"])
_CHOOSE_PLAN = frozenset(
    [
        "pode ser esse",
        "pode ser",
        "esse",
        "esse mesmo",
        "quero esse",
        "vou levar",
        "fechado",
        "pode ser esse mesmo",
        "esse ai",
        "beleza",
        "vamos nesse",
        "bora",
    ]
)
_CHECKOUT_YES = frozenset(
    [
        "sim",
        "quero",
        "pode",
        "pode sim",
        "pode mandar",
        "manda",
        "mande",
        "claro",
        "bora",
        "ok",
        "isso",
        "fechado",
        "sim pode",
        "sim quero",
        "quero sim",
    ]
)


def demo_close_sale_reply(
    question: str, previous_bot: str, offer_rows: Sequence[Mapping[str, Any]]
) -> str | None:
    """Leva a conversa até o link de pagamento do plano escolhido, sem modelo e sem pessoa.

    Dispara com intenção de compra ("quero contratar o Escala", "manda o link"), ou com um
    "sim" logo depois de a AnaClara oferecer o link. O plano vem do que o cliente citou ou do
    plano de que a AnaClara vinha falando; sem plano definido, ela pergunta qual.
    """
    phrase = normalize(question).strip()
    words = set(phrase.split())
    wants = bool(
        (words & _BUY_VERBS and words & _BUY_SUBJECTS) or (words & _SEND_VERBS and "link" in words)
    )
    prev_norm = normalize(previous_bot)
    offered_link = "enviar o link" in prev_norm
    recommended = any(k in prev_norm for k in ("recomendo", "e o ideal", "plano ideal"))
    short = phrase.strip("?!., ")
    said_yes = (offered_link and short in _CHECKOUT_YES) or (
        (offered_link or recommended) and short in _CHOOSE_PLAN
    )
    if not wants and not said_yes:
        return None

    def mentioned(text: str) -> list[Mapping[str, Any]]:
        return [
            o
            for o in offer_rows
            if isinstance(o.get("name"), str)
            and re.search(rf"(?<![a-z0-9]){re.escape(normalize(o['name']))}(?![a-z0-9])", text)
        ]

    chosen = mentioned(phrase)
    if len(chosen) != 1:
        chosen = mentioned(prev_norm)
    if len(chosen) == 1:
        return _demo_checkout_text(chosen[0])
    names = [str(o["name"]) for o in offer_rows if isinstance(o.get("name"), str)]
    if len(names) == 3:
        return (
            f"Ótimo! Qual plano você prefere: {names[0]}, {names[1]} ou {names[2]}? "
            "Se quiser, me diga quantas respostas de IA espera por mês que eu indico o ideal."
        )
    return None


def demo_verified_commercial_reply(question: str) -> str | None:
    """Answer narrow product/purchase intents from proven capabilities, never from model guesses.

    This is available only inside the authenticated single-contact FM demo path,
    after tenant, opt-out, subscription, trial, and reply-rate checks.
    """
    phrase = normalize(question)
    words = set(phrase.split())
    checkout_terms = {"link", "links", "pagamento", "pagamentos", "checkout", "pix", "cobranca"}
    sales_terms = {
        "vende",
        "vender",
        "venda",
        "vendas",
        "produto",
        "produtos",
        "oferta",
        "ofertas",
        "atende",
        "atender",
        "atendimento",
    }
    channel_terms = {"whatsapp", "facebook", "instagram", "messenger", "canais"}
    buying = (
        "vou fechar" in phrase
        or "quero fechar" in phrase
        or "quero contratar" in phrase
        or "quero assinar" in phrase
        or "vou pagar" in phrase
        or (
            bool(words & {"manda", "mande", "envia", "enviar", "passe", "passa"})
            and bool(words & {"link", "checkout"})
        )
    )
    if buying:
        return (
            "Entendi que você quer contratar o AtendeVendeIA! Me diga qual plano você "
            "prefere (Essencial, Profissional ou Escala) que eu te envio o link de "
            f"pagamento. Informações oficiais: {DEMO_PAGE}"
        )
    # O piloto bloqueia mensagens de saída que mencionam certos identificadores
    # financeiros, inclusive quando o modelo os usa em frases negativas. Responda
    # perguntas sobre essa modalidade com texto comercial aprovado e compatível
    # com o mesmo filtro, sem permitir que o LLM improvise dados de cobrança.
    if "pix" in words or (
        bool(words & {"pagamento", "pagamentos", "cobranca", "cobrancas", "checkout"})
        and bool(words & {"como", "funciona", "funcionar", "via", "aceita", "receber"})
    ):
        return (
            "Para pagamentos por transferência instantânea, o AtendeVendeIA "
            "pode compartilhar o link externo que sua empresa cadastrou na "
            "oferta, caso o provedor de cobrança ofereça essa modalidade. "
            "O sistema não cria cobranças, não recebe valores nem confirma "
            "recebimentos: essas etapas acontecem no provedor externo. "
            "Quer saber como cadastrar o link na oferta?"
        )
    payment_question = bool(words & checkout_terms) and bool(
        words & (sales_terms | {"gera", "gerar", "cria", "criar", "receber", "pagar"})
    )
    feature_question = bool(words & (sales_terms | channel_terms)) and bool(
        words
        & {
            "faz",
            "fazer",
            "funciona",
            "funcionalidade",
            "funcionalidades",
            "oferece",
            "permite",
            "consegue",
            "pode",
            "vai",
            "gera",
            "gerar",
            "vender",
            "vende",
            "venda",
            "vendas",
            "atende",
            "atender",
            "alem",
            "cadastrados",
            "cadastrar",
            "cadastro",
            "produto",
            "produtos",
            "facebook",
            "instagram",
            "messenger",
            "canais",
        }
    )
    if payment_question:
        return (
            "Sim. O AtendeVendeIA pode atender clientes, apresentar ofertas "
            "cadastradas e compartilhar links de pagamento que a empresa "
            "configurou previamente. Não cria links de cobrança, não processa "
            "pagamentos e não confirma vendas sozinho. O atendimento por "
            "WhatsApp, Messenger e Instagram depende das conexões habilitadas. "
            "Quer saber como cadastrar suas ofertas?"
        )
    if feature_question:
        return (
            "O AtendeVendeIA atende dúvidas e ajuda a vender apresentando "
            "produtos e ofertas cadastrados, inclusive compartilhando um link "
            "externo previamente configurado. Também oferece Conversas, "
            "Recuperação e Relatórios. WhatsApp, Messenger do Facebook e "
            "Instagram dependem de conexão e configuração; ele não gera "
            "checkout nem realiza cobrança. Quer conhecer o fluxo de venda?"
        )
    return None


@dataclass(frozen=True)
class Proposal:
    """O que o vendedor faria com a conversa, sem efeito colateral (nada é gravado nem enviado).

    `failed` = o modelo não respondeu (rede, cota, formato); `kind` é "reply" ou "handoff".
    Usada pelo vendedor de verdade, pela avaliação (`ai-eval`) e pela tela "Testar conversa".
    """

    kind: str
    text: str = ""
    reason: str | None = None
    failed: bool = False
    tokens_in: int = 0
    tokens_out: int = 0


def propose(model: AiModel, ctx: AiContext, registry: dict[str, tuple[str, str, str]]) -> Proposal:
    """Chama o modelo e aplica as travas do sistema: só texto sem URL/valor livre, com os dados da
    oferta vindos do cadastro (`registry`: id -> nome, preço formatado, link)."""
    try:
        reply = model.reply(ctx)
    except Exception as exc:
        # Sem isto a transação voltaria e a mesma mensagem seria tentada para sempre, sem resposta.
        log.warning(
            "modelo falhou", extra={"ctx": {"erro": type(exc).__name__, "motivo": str(exc)[:120]}}
        )
        return Proposal("handoff", reason="erro_do_modelo", failed=True)
    if reply.handoff:
        return Proposal(
            "handoff",
            reason=reply.handoff_reason or "modelo_pediu",
            tokens_in=reply.tokens_in,
            tokens_out=reply.tokens_out,
        )
    text = render_reply(reply, registry)
    if text is None:
        return Proposal(
            "handoff",
            reason="resposta_invalida",
            tokens_in=reply.tokens_in,
            tokens_out=reply.tokens_out,
        )
    return Proposal("reply", text=text, tokens_in=reply.tokens_in, tokens_out=reply.tokens_out)


def _handoff(
    conn: Conn, tenant_id: uuid.UUID, conv_id: uuid.UUID, reason: str, notify: bool
) -> None:
    conn.execute(
        "UPDATE conversations SET status = 'human', handoff_reason = %s WHERE id = %s",
        (reason, conv_id),
    )
    if notify:
        enqueue_text(conn, tenant_id, conv_id, "bot", HANDOFF_MESSAGE)


def run_ai_replies(
    db: Database,
    model: AiModel,
    *,
    now: datetime | None = None,
    limit: int = 50,
    pilot_settings: Settings | None = None,
) -> SellerStats:
    now = now or datetime.now(UTC)
    stats = SellerStats()
    with db.tx(system=True) as conn:
        pending = conn.execute(
            "SELECT DISTINCT m.tenant_id, m.conversation_id FROM messages m "
            "JOIN conversations cv ON cv.id = m.conversation_id "
            "WHERE m.direction = 'in' AND NOT m.handled AND cv.status = 'bot' LIMIT %s",
            (limit,),
        ).fetchall()
    for row in pending:
        try:
            with db.tx(tenant_id=row["tenant_id"]) as conn:
                outcome = _handle_conversation(
                    conn,
                    model,
                    row["tenant_id"],
                    row["conversation_id"],
                    now,
                    pilot_settings=pilot_settings,
                )
        except Exception:
            log.exception(
                "erro no vendedor IA", extra={"ctx": {"conv": str(row["conversation_id"])}}
            )
            continue
        stats.conversations += 1
        setattr(stats, outcome, getattr(stats, outcome) + 1)
    return stats


def _handle_conversation(
    conn: Conn,
    model: AiModel,
    tenant_id: uuid.UUID,
    conv_id: uuid.UUID,
    now: datetime,
    *,
    pilot_settings: Settings | None = None,
) -> str:
    locked = conn.execute(
        "SELECT cv.status, cv.contact_id, cv.channel, ct.name, ct.phone, cc.external_id "
        "FROM conversations cv JOIN contacts ct ON ct.id = cv.contact_id "
        "LEFT JOIN contact_channels cc ON cc.contact_id = ct.id AND cc.channel = cv.channel "
        "WHERE cv.id = %s FOR UPDATE OF cv SKIP LOCKED",
        (conv_id,),
    ).fetchone()
    if locked is None or locked["status"] != "bot":
        return "ignored"
    identity = (
        locked["phone"]
        if locked["channel"] == "whatsapp"
        else identity_of(locked["channel"], locked["external_id"] or "")
    )
    inbound = conn.execute(
        "UPDATE messages SET handled = true WHERE conversation_id = %s AND direction = 'in' "
        "AND NOT handled RETURNING id, body, created_at",
        (conv_id,),
    ).fetchall()
    # SQL UPDATE ... RETURNING has no row-order guarantee. The latest customer
    # request determines the reply when several incoming messages accumulated.
    inbound.sort(key=lambda row: (row["created_at"], str(row["id"])))
    if not inbound:
        return "ignored"
    # "vc", "msg", "q"...: as camadas sem modelo casam palavras exatas, então leem o texto com
    # as abreviações expandidas. A mensagem original segue guardada e é a que o modelo lê.
    for message in inbound:
        message["body"] = expand_abbreviations(message["body"])
    pilot = locked["channel"] == "whatsapp" and pilot_contact(
        pilot_settings, tenant_id, locked["phone"]
    )
    demo = bool(pilot and pilot_settings and pilot_settings.pilot_demo_enabled)
    if pilot and not demo and (len(inbound) != 1 or not greeting_only(inbound[0]["body"])):
        _handoff(conn, tenant_id, conv_id, "piloto_somente_saudacao", notify=False)
        return "handoffs"
    if conn.execute(
        "SELECT 1 FROM suppressions WHERE tenant_id = %s AND identity = %s",
        (tenant_id, identity),
    ).fetchone():
        return "ignored"

    # Transaction lock serializes IA quota checks across workers for this tenant.
    # Unlike SELECT FOR UPDATE, it does not depend on UPDATE RLS of tenant_plans.
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"fmav:ai:quota:{tenant_id}",),
    )
    plan = conn.execute(
        "SELECT status FROM tenant_plans WHERE tenant_id = %s",
        (tenant_id,),
    ).fetchone()
    if plan is None or blocks_service(plan["status"]):
        _handoff(conn, tenant_id, conv_id, "assinatura_inativa", notify=False)
        return "handoffs"
    if trial.blocked(conn, tenant_id, now=now):
        _handoff(conn, tenant_id, conv_id, "trial_limit_or_expired", notify=False)
        return "handoffs"

    settings = conn.execute(
        "SELECT ai_enabled, ai_persona, timezone FROM tenant_settings WHERE tenant_id = %s",
        (tenant_id,),
    ).fetchone()
    if settings is None or (not settings["ai_enabled"] and not pilot):
        _handoff(conn, tenant_id, conv_id, "ia_desligada", notify=False)
        return "handoffs"
    if any(asks_for_human(r["body"]) for r in inbound):
        _handoff(conn, tenant_id, conv_id, "pediu_atendente", notify=True)
        return "handoffs"
    recent = conn.execute(
        "SELECT count(*) AS c FROM messages WHERE conversation_id = %s AND direction = 'out' "
        "AND author = 'bot' AND status <> 'failed' AND created_at > %s",
        (conv_id, now - timedelta(hours=1)),
    ).fetchone()
    assert recent is not None
    hourly_ceiling = (
        MAX_PILOT_DEMO_REPLIES_PER_HOUR
        if (
            locked["channel"] == "whatsapp"
            and pilot_contact(pilot_settings, tenant_id, locked["phone"])
            and pilot_settings
            and pilot_settings.pilot_demo_enabled
        )
        else MAX_BOT_REPLIES_PER_HOUR
    )
    if recent["c"] >= hourly_ceiling:
        _handoff(conn, tenant_id, conv_id, "limite_de_respostas", notify=False)
        return "handoffs"

    # O piloto FM não usa ofertas fictícias nem cria checkout: somente os planos
    # vigentes do catálogo comercial canônico e a landing informativa pública.
    if demo:
        offer_rows = conn.execute(
            "SELECT key AS id, display_name AS name, "
            "('Franquia mensal de ' || (limits->>'ai_replies_per_month') || "
            "' respostas de IA, compartilhada nos canais. Contratação assistida.') AS description, "
            "price_monthly_cents AS price_cents, "
            "limits->>'ai_replies_per_month' AS quota FROM plans "
            "WHERE key IN ('av-essencial', 'av-profissional', 'av-escala') "
            "AND active AND price_monthly_cents > 0 ORDER BY phase"
        ).fetchall()
        if len(offer_rows) != 3:
            _handoff(conn, tenant_id, conv_id, "catalogo_fm_incompleto", notify=False)
            return "handoffs"
        paid = conn.execute(
            "SELECT name, payment_url FROM offers WHERE tenant_id = %s AND active "
            "AND payment_url LIKE 'https://%%'",
            (tenant_id,),
        ).fetchall()

        def _checkout_for(plan_key: str) -> str | None:
            suffix = plan_key.removeprefix("av-")
            found = [
                str(x["payment_url"])
                for x in paid
                if normalize(str(x["name"])).split()[-1:] == [suffix]
            ]
            return found[0] if len(found) == 1 else None

        offer_rows = [
            {**dict(o), "payment_url": DEMO_PAGE, "checkout_url": _checkout_for(str(o["id"]))}
            for o in offer_rows
        ]
    elif pilot:
        offer_rows = []
    else:
        offer_rows = conn.execute(
            "SELECT id, name, description, price_cents, payment_url FROM offers "
            "WHERE tenant_id = %s AND active ORDER BY created_at LIMIT 20",
            (tenant_id,),
        ).fetchall()
    if not offer_rows and not pilot:
        _handoff(conn, tenant_id, conv_id, "sem_ofertas", notify=True)
        return "handoffs"
    # Authorized pilot only: a greeting must never depend on a free-form model
    # response, and recent inbound messages can be batched after manual resume.
    # Use the latest question without discarding the preceding messages from
    # the model history or relaxing opt-out/human/tenant checks above.
    pilot_question = inbound[-1]["body"] if demo else None
    if demo and pilot_question is not None and greeting_only(pilot_question):
        enqueue_text(
            conn,
            tenant_id,
            conv_id,
            "bot",
            "Olá! Sou a AnaClara, assistente virtual da FM Tecnologia IA. "
            "Como posso ajudar você com o AtendeVendeIA?",
        )
        conn.execute("UPDATE conversations SET last_message_at = now() WHERE id = %s", (conv_id,))
        return "replied"

    # A model reply has only one offer_id; never ask it to invent a three-plan price table.
    # This narrow FM pilot answer comes exclusively from the live canonical plan catalogue.
    if demo and pilot_question is not None and demo_plan_listing_requested(pilot_question):
        lines = [
            "Os planos do AtendeVendeIA têm as mesmas funcionalidades. "
            "O que muda é a franquia mensal:"
        ]
        for offer in offer_rows:
            quota = str(offer["quota"] or "")
            if not quota.isdigit() or int(quota) <= 0 or not offer["name"]:
                _handoff(conn, tenant_id, conv_id, "catalogo_fm_incompleto", notify=False)
                return "handoffs"
            pretty_quota = f"{int(quota):,}".replace(",", ".")
            lines.append(
                f"{offer['name']}: {format_brl(offer['price_cents'])}/mês, "
                f"{pretty_quota} respostas de IA por mês."
            )
        lines.append("Detalhes e demonstração: " + DEMO_PAGE)
        text = "\n".join(lines)
        if len(text) > 850:
            _handoff(conn, tenant_id, conv_id, "catalogo_fm_incompleto", notify=False)
            return "handoffs"
        enqueue_text(conn, tenant_id, conv_id, "bot", text)
        conn.execute("UPDATE conversations SET last_message_at = now() WHERE id = %s", (conv_id,))
        return "replied"
    if demo and pilot_question is not None:
        prev_bot = conn.execute(
            "SELECT body FROM messages WHERE conversation_id = %s AND direction = 'out' "
            "AND author = 'bot' ORDER BY created_at DESC LIMIT 1",
            (conv_id,),
        ).fetchone()
        closing = demo_close_sale_reply(
            pilot_question, prev_bot["body"] if prev_bot else "", offer_rows
        )
        if closing is not None and len(closing) <= 850:
            enqueue_text(conn, tenant_id, conv_id, "bot", closing)
            conn.execute(
                "UPDATE conversations SET last_message_at = now() WHERE id = %s", (conv_id,)
            )
            return "replied"
    # Small, deterministic qualification: don't hand off when the contact asks for
    # advice without first supplying the monthly usage needed to choose a tier.
    if demo and pilot_question is not None:
        question = pilot_question
        recommends = demo_recommendation_requested(question)
        stated_volume = demo_requested_volume(question)
        previous = conn.execute(
            "SELECT body FROM messages WHERE conversation_id = %s AND direction = 'out' "
            "AND author = 'bot' ORDER BY created_at DESC LIMIT 1",
            (conv_id,),
        ).fetchone()
        answering_volume = bool(previous and asked_for_volume(previous["body"]))
        carried: int | None = None
        if (
            previous
            and previous["body"] == DEMO_MESSAGES_CLARIFICATION
            and normalize(question) in _ALL_OF_THEM
        ):
            for earlier in conn.execute(
                "SELECT body FROM messages WHERE conversation_id = %s AND direction = 'in' "
                "ORDER BY created_at DESC LIMIT 8",
                (conv_id,),
            ).fetchall():
                carried = demo_requested_volume(expand_abbreviations(earlier["body"]))
                if carried is not None:
                    break
        if recommends or answering_volume or stated_volume is not None or carried is not None:
            volume = (
                carried
                if carried is not None
                else demo_requested_volume(question, direct_answer=answering_volume)
            )
            if volume is None:
                # O cliente já disse o volume antes? Não pergunta de novo.
                for earlier in conn.execute(
                    "SELECT body FROM messages WHERE conversation_id = %s AND direction = 'in' "
                    "ORDER BY created_at DESC LIMIT 8 OFFSET 1",
                    (conv_id,),
                ).fetchall():
                    volume = demo_requested_volume(expand_abbreviations(earlier["body"]))
                    if volume is not None:
                        break
            words = set(normalize(question).split())
            # Incoming WhatsApp messages are not the same as monthly AI replies.
            # A prospect volunteering their inbox volume needs one clarification,
            # not an invented usage estimate or an unsafe LLM price.
            if (
                volume is not None
                and words & {"mensagens", "mensagem", "msgs", "msg", "atendimentos"}
                and "respostas" not in words
            ):
                text = DEMO_MESSAGES_CLARIFICATION
            elif volume is None:
                text = DEMO_VOLUME_QUESTION
            else:
                tiers = sorted(
                    (
                        (int(o["quota"]), o)
                        for o in offer_rows
                        if o["quota"] and str(o["quota"]).isdigit()
                    ),
                    key=lambda entry: entry[0],
                )
                if len(tiers) != 3:
                    _handoff(conn, tenant_id, conv_id, "catalogo_fm_incompleto", notify=False)
                    return "handoffs"
                suitable = next((o for cap, o in tiers if volume <= cap), None)
                if suitable is None:
                    text = (
                        "Esse volume supera as franquias disponíveis. "
                        "Posso encaminhar sua necessidade à equipe da FM Tecnologia IA."
                    )
                else:
                    recommended_text = _demo_recommendation_line(suitable)
                    if recommended_text is None:
                        _handoff(conn, tenant_id, conv_id, "catalogo_fm_incompleto", notify=False)
                        return "handoffs"
                    text = recommended_text
            enqueue_text(conn, tenant_id, conv_id, "bot", text)
            conn.execute(
                "UPDATE conversations SET last_message_at = now() WHERE id = %s", (conv_id,)
            )
            return "replied"

    # Verified FM product questions and purchase intent must not be sent to a
    # generic store-sales prompt. There is no live checkout in this demo.
    if demo and pilot_question is not None:
        verified = demo_verified_commercial_reply(pilot_question)
        if verified is not None and len(verified) <= 850:
            enqueue_text(conn, tenant_id, conv_id, "bot", verified)
            conn.execute(
                "UPDATE conversations SET last_message_at = now() WHERE id = %s", (conv_id,)
            )
            return "replied"

    # A simple affirmative after a plan recommendation must continue commercial
    # qualification, not force the Gemini model to recite guarded prices or URLs.
    if demo and pilot_question is not None:
        last_bot = conn.execute(
            "SELECT body FROM messages WHERE conversation_id = %s AND direction = 'out' "
            "AND author = 'bot' ORDER BY created_at DESC LIMIT 1",
            (conv_id,),
        ).fetchone()
        details = demo_plan_details_reply(
            pilot_question, last_bot["body"] if last_bot else "", offer_rows
        )
        if details is not None and len(details) <= 850:
            enqueue_text(conn, tenant_id, conv_id, "bot", details)
            conn.execute(
                "UPDATE conversations SET last_message_at = now() WHERE id = %s", (conv_id,)
            )
            return "replied"

    # A definite price/link intent and a unique name can be served from registered
    # commercial data at zero model tokens. Preserve the regular plan/tenant guards,
    # opt-out, human handoff and hourly rate limit above this branch.
    if len(inbound) == 1 or demo:
        cached = catalog_offer_reply(
            pilot_question if pilot_question is not None else inbound[0]["body"],
            offer_rows,
            demo=demo,
        )
        if cached is not None:
            enqueue_text(conn, tenant_id, conv_id, "bot", cached)
            conn.execute(
                "UPDATE conversations SET last_message_at = now() WHERE id = %s", (conv_id,)
            )
            return "replied"

    # Base de conhecimento do cliente: pergunta já cadastrada é respondida sem modelo (zero
    # token, sem risco de timeout). No piloto de saudação nada além da saudação é permitido.
    knowledge = (
        ()
        if (pilot and not demo)
        else tuple(
            (k["question"], k["answer"])
            for k in conn.execute(
                "SELECT question, answer FROM knowledge_items WHERE active "
                "ORDER BY created_at LIMIT 40"
            ).fetchall()
        )
    )
    if knowledge:
        known = knowledge_reply(inbound[-1]["body"], knowledge)
        if known is not None:
            enqueue_text(conn, tenant_id, conv_id, "bot", known)
            conn.execute(
                "UPDATE conversations SET last_message_at = now() WHERE id = %s", (conv_id,)
            )
            return "replied"

    # Only real model calls consume ai_replies_per_month; deterministic replies
    # remain governed by outbound/trial metering in the canonical outbox.
    if not model.available:
        _handoff(conn, tenant_id, conv_id, "ia_indisponivel", notify=False)
        return "handoffs"
    tz = settings["timezone"]
    limit = usage.monthly_limit(conn, tenant_id)
    if limit is None or usage.month_usage(conn, tenant_id, tz)["calls"] >= limit:
        _handoff(conn, tenant_id, conv_id, "limite_do_plano", notify=True)
        return "handoffs"

    registry = {
        str(o["id"]): (o["name"], format_brl(o["price_cents"]), o["payment_url"])
        for o in offer_rows
    }
    history = conn.execute(
        "SELECT direction, body FROM messages WHERE conversation_id = %s "
        "ORDER BY created_at DESC LIMIT %s",
        (conv_id, HISTORY),
    ).fetchall()
    ctx = AiContext(
        persona=settings["ai_persona"],
        customer_name=(locked["name"] or "").split(" ")[0],
        offers=tuple(
            OfferView(str(o["id"]), o["name"], o["description"], format_brl(o["price_cents"]))
            for o in offer_rows
        ),
        history=(
            (("customer", inbound[0]["body"]),)
            if pilot and not demo
            else tuple(
                ("customer" if h["direction"] == "in" else "assistant", h["body"])
                for h in reversed(history)
            )
        ),
        pilot_greeting=pilot and not demo,
        pilot_demo=demo,
        knowledge=knowledge,
    )
    proposal = propose(model, ctx, registry)
    if proposal.failed:
        usage.record(conn, tenant_id, tz, ok=False)
        # Falha técnica não é motivo para tirar a conversa da automação: avisa o cliente e
        # tenta de novo na próxima mensagem. Só entrega a uma pessoa se repetir.
        waits = conn.execute(
            "SELECT count(*) AS c FROM messages WHERE conversation_id = %s AND direction = 'out' "
            "AND author = 'bot' AND body = %s AND created_at > %s",
            (conv_id, MODEL_RETRY_MESSAGE, now - timedelta(hours=1)),
        ).fetchone()
        assert waits is not None
        if waits["c"] >= 2:
            _handoff(conn, tenant_id, conv_id, "erro_do_modelo", notify=True)
            return "handoffs"
        enqueue_text(conn, tenant_id, conv_id, "bot", MODEL_RETRY_MESSAGE)
        conn.execute("UPDATE conversations SET last_message_at = now() WHERE id = %s", (conv_id,))
        return "replied"
    usage.record(
        conn, tenant_id, tz, ok=True, tokens_in=proposal.tokens_in, tokens_out=proposal.tokens_out
    )
    if proposal.kind == "handoff" and demo and proposal.reason == "resposta_invalida":
        # O texto do modelo foi barrado (valor/link livre). Na demonstração a cliente não é
        # transferida: a AnaClara refaz o convite com dados do cadastro.
        enqueue_text(conn, tenant_id, conv_id, "bot", DEMO_SAFE_FALLBACK)
        conn.execute("UPDATE conversations SET last_message_at = now() WHERE id = %s", (conv_id,))
        return "replied"
    if proposal.kind == "handoff":
        _handoff(conn, tenant_id, conv_id, proposal.reason or "modelo_pediu", notify=True)
        return "handoffs"
    text = proposal.text
    enqueue_text(conn, tenant_id, conv_id, "bot", text)
    conn.execute("UPDATE conversations SET last_message_at = now() WHERE id = %s", (conv_id,))
    return "replied"
