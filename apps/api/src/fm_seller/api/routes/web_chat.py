"""Widget de chat embutido em site institucional da própria FM (ou de outro tenant que o
assine). Sem cookie de conta: a identidade é o `site` (mapeado para um tenant em
`FM_WEB_CHAT_TENANTS`) e um `session_id` que o widget mantém no navegador do visitante.

CORS desta rota é tratado à parte (`WebChatCorsMiddleware`, em `api.guards`), restrito às
origens de `FM_WEB_CHAT_ORIGINS` — não usa o CORS geral da API (que é da origem única do
painel). Desligado por padrão (`FM_WEB_CHAT_ENABLED=false`): a rota responde 404.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from fm_seller.ai.seller import handle_conversation_now
from fm_seller.channels.web import ingest_web_message, latest_reply, valid_session_id
from fm_seller.errors import bad_request, not_found

router = APIRouter(tags=["web_chat"])

MAX_MESSAGE = 2000


class WebChatIn(BaseModel):
    site: str = Field(min_length=1, max_length=32)
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE)


class WebChatOut(BaseModel):
    reply: str
    handoff: bool


@router.post("/web/chat", response_model=WebChatOut)
def chat(body: WebChatIn, request: Request) -> WebChatOut:
    cfg = request.app.state.settings
    if not cfg.web_chat_enabled:
        raise not_found()
    tenant_map = cfg.web_chat_tenant_map
    tenant_raw = tenant_map.get(body.site)
    if tenant_raw is None:
        raise not_found("Site não reconhecido.")
    if not valid_session_id(body.session_id):
        raise bad_request("invalid_session_id", "Sessão inválida.")
    tenant_id = uuid.UUID(tenant_raw)
    message_id = uuid.uuid4().hex

    db = request.app.state.db
    with db.tx(tenant_id=tenant_id) as conn:
        conv_id, _ = ingest_web_message(
            conn,
            tenant_id,
            session_id=body.session_id,
            text=body.message,
            message_id=message_id,
        )

    handle_conversation_now(db, request.app.state.ai_model, tenant_id, conv_id)

    with db.tx(tenant_id=tenant_id) as conn:
        result = latest_reply(conn, conv_id)
    if result is None:
        # Não esperado (handle_conversation_now sempre grava algo nesse caminho), mas o
        # widget tem o próprio fallback de WhatsApp: melhor isso do que um 500 sem contexto.
        return WebChatOut(
            reply="Desculpe, não consegui responder agora. Posso te atender pelo WhatsApp?",
            handoff=True,
        )
    text, handoff = result
    return WebChatOut(reply=text, handoff=handoff)
