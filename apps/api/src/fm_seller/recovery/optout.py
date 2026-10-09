"""Pedido de não contato por mensagem. Falso positivo só silencia; falso negativo viola a LGPD."""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime

from fm_seller.db import Conn

_WORDS = {"sair", "parar", "pare", "stop", "descadastrar", "unsubscribe", "cancelar"}
_PHRASES = ("nao quero receber", "nao quero mais receber", "nao me envie", "nao mande mais")
_MAX_LEN = 60  # mensagens curtas: basta uma palavra de saída ("sair", "parar") ou uma frase
_MAX_LEN_LONG = 400  # mensagens longas (ex.: áudio transcrito): só frases claras de saída
# Frases claras de saída, procuradas também em mensagem longa. Só entram formas que pedem para
# NÃO receber mais contato; uma palavra solta ("parar") em frase longa continua sendo conversa.
_STRONG = tuple(
    re.compile(p)
    for p in (
        r"\bpar(a|e|ar) de (me )?(mandar|enviar|escrever|chamar|ligar|procurar)\b",
        r"\bnao quero mais receber\b",
        r"\bnao quero (mais )?receber (mais )?(mensagem|mensagens|msg|contato|contatos|ligacao"
        r"|ligacoes|promocao|promocoes|nada)\b",
        r"\bnao quero (mais )?(ser contatad[oa]|contato|mensagem|mensagens)\b",
        r"\bnao me (envie|mande|chame|ligue|procure|escreva)\b",
        r"\bnao (envie|mande) mais\b",
        r"\b(tir[ae]|tirar|remov[ae]|remover|retir[ae]|retirar|exclu[ia]|excluir)"
        r"( o| meu| minha)*( numero| contato| nome| telefone)? (da|de) (sua |essa |esta |nossa )?"
        r"(lista|base)\b",
        r"\bsair da (sua |essa |nossa )?(lista|base)\b",
        r"\bdescadastr",
    )
)


def normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", plain.lower()).strip()


def is_opt_out(text: str) -> bool:
    norm = normalize(text)
    if not norm or len(norm) > _MAX_LEN_LONG:
        return False
    if any(rx.search(norm) for rx in _STRONG):
        return True
    if len(norm) > _MAX_LEN:
        return False
    return bool(set(norm.split()) & _WORDS) or any(p in norm for p in _PHRASES)


def suppress_identity(
    conn: Conn, tenant_id: uuid.UUID, identity: str, reason: str, now: datetime | None = None
) -> None:
    """Bloqueia o contato e encerra o que estava em andamento para ele."""
    conn.execute(
        "INSERT INTO suppressions (tenant_id, identity, reason) VALUES (%s, %s, %s) "
        "ON CONFLICT (tenant_id, identity) DO NOTHING",
        (tenant_id, identity, reason),
    )
    conn.execute(
        "UPDATE recovery_steps SET status = 'canceled', detail = 'nao_contatar' "
        "WHERE status = 'scheduled' AND case_id IN (SELECT c.id FROM recovery_cases c "
        "JOIN contacts ct ON ct.id = c.contact_id WHERE c.status = 'open' "
        "AND (ct.phone = %s OR lower(ct.email) = %s))",
        (identity, identity),
    )
    conn.execute(
        "UPDATE recovery_cases SET status = 'stopped', closed_reason = 'opt_out', "
        "closed_at = COALESCE(%s, now()) WHERE status = 'open' AND contact_id IN ("
        "SELECT id FROM contacts WHERE phone = %s OR lower(email) = %s)",
        (now, identity, identity),
    )
