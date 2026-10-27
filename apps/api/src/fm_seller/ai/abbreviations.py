"""Abreviações comuns de WhatsApp ("vc", "msg", "q", "pq"...) para as camadas sem modelo.

O modelo entende essas formas sozinho. As camadas determinísticas (base de conhecimento,
volume, planos, ofertas) casam palavras exatas, então recebem o texto já expandido. O texto
original do cliente continua guardado e é o que o modelo lê. Só formas sem ambiguidade entram
aqui: "n" (não?), "dnd" (de nada?) e afins ficam de fora de propósito.
"""

from __future__ import annotations

import re

ABBREVIATIONS: dict[str, str] = {
    "vc": "você",
    "vcs": "vocês",
    "msg": "mensagem",
    "msgs": "mensagens",
    "q": "que",
    "oq": "o que",
    "pq": "porque",
    "tb": "também",
    "tbm": "também",
    "qdo": "quando",
    "qnd": "quando",
    "qto": "quanto",
    "qtos": "quantos",
    "qta": "quanta",
    "qtas": "quantas",
    "hj": "hoje",
    "agr": "agora",
    "dps": "depois",
    "mt": "muito",
    "mto": "muito",
    "mta": "muita",
    "td": "tudo",
    "tds": "todos",
    "blz": "beleza",
    "obg": "obrigado",
    "vlw": "valeu",
    "pfv": "por favor",
    "pf": "por favor",
    "vlr": "valor",
    "vlrs": "valores",
    "info": "informação",
    "infos": "informações",
    "ql": "qual",
}

_TOKEN = re.compile(r"(?<![A-Za-zÀ-ÿ])[A-Za-zÀ-ÿ]+(?![A-Za-zÀ-ÿ])")


def expand_abbreviations(text: str) -> str:
    """Troca abreviações inteiras pela forma completa, sem tocar no resto do texto."""

    def swap(match: re.Match[str]) -> str:
        full = ABBREVIATIONS.get(match.group(0).lower())
        if full is None:
            return match.group(0)
        # "1500msg": mantém o número separado da palavra.
        glued = match.start() > 0 and text[match.start() - 1].isdigit()
        return (" " if glued else "") + full

    return _TOKEN.sub(swap, text)
