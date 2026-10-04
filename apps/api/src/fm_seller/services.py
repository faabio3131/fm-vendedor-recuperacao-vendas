"""Regras de negócio da fundação: login, sessão, planos e conexões."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fm_seller.auth.google import GoogleIdentity
from fm_seller.config import Settings
from fm_seller.db import Conn, Database
from fm_seller.errors import AppError, bad_request, forbidden, not_found, unauthorized
from fm_seller.providers.catalog import Provider, get_provider, mask_secret
from fm_seller.providers.testers import ConnectionTester
from fm_seller.security.crypto import CryptoError, SecretBox

EDIT_ROLES = ("owner", "admin")
MAX_SESSIONS = 10  # sessões ativas por pessoa; a mais antiga é encerrada ao passar disso
NO_ACCESS = "Esta conta Google não tem acesso. Use o e-mail da compra do plano."


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def audit(
    conn: Conn,
    *,
    tenant_id: uuid.UUID,
    actor: uuid.UUID | None,
    action: str,
    target: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (tenant_id, actor_user_id, action, target, detail) "
        "VALUES (%s, %s, %s, %s, %s::jsonb)",
        (tenant_id, actor, action, target, json.dumps(detail or {})),
    )


# ---------------------------------------------------------------- login e sessão


@dataclass(frozen=True)
class Membership:
    tenant_id: uuid.UUID
    tenant_name: str
    role: str


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    email: str
    name: str
    memberships: tuple[Membership, ...]
    tenant_id: uuid.UUID
    role: str


def login_with_google(db: Database, settings: Settings, identity: GoogleIdentity) -> str:
    """Cria a sessão de quem tem acesso (membro existente ou convite da compra). Devolve o token."""
    if not identity.email_verified:
        raise unauthorized("O e-mail da conta Google não está verificado.")
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)
    with db.tx(google_sub=identity.sub, login_email=identity.email) as conn:
        user = conn.execute(
            "SELECT id FROM users WHERE google_sub = %s", (identity.sub,)
        ).fetchone()
        invite = conn.execute(
            "SELECT id, tenant_id, role FROM pending_invites "
            "WHERE lower(email) = lower(%s) AND accepted_at IS NULL AND expires_at > now() "
            "ORDER BY created_at LIMIT 1",
            (identity.email,),
        ).fetchone()
        admin_invite = conn.execute(
            "SELECT id FROM platform_admin_invites "
            "WHERE lower(email) = lower(%s) AND accepted_at IS NULL AND expires_at > now() "
            "ORDER BY created_at LIMIT 1",
            (identity.email,),
        ).fetchone()
        if user is None:
            if invite is None and admin_invite is None:
                raise forbidden(NO_ACCESS)
            user = conn.execute(
                "INSERT INTO users (google_sub, email, name) VALUES (%s, %s, %s) RETURNING id",
                (identity.sub, identity.email, identity.name),
            ).fetchone()
        assert user is not None
        user_id: uuid.UUID = user["id"]
        db.set_user(conn, user_id)
        if invite is not None:
            db.set_tenant(conn, invite["tenant_id"])
            conn.execute(
                "INSERT INTO memberships (tenant_id, user_id, role) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (invite["tenant_id"], user_id, invite["role"]),
            )
            conn.execute(
                "UPDATE pending_invites SET accepted_at = now() WHERE id = %s", (invite["id"],)
            )
            audit(
                conn,
                tenant_id=invite["tenant_id"],
                actor=user_id,
                action="invite.accepted",
                target=identity.email,
            )
        if admin_invite is not None:
            # Convite criado pela linha de comando: a pessoa passa a ser da equipe da plataforma.
            conn.execute(
                "INSERT INTO platform_admins (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (user_id,),
            )
            conn.execute(
                "UPDATE platform_admin_invites SET accepted_at = now() WHERE id = %s",
                (admin_invite["id"],),
            )
        has_access = conn.execute(
            "SELECT 1 WHERE EXISTS (SELECT 1 FROM memberships WHERE user_id = %s) "
            "OR EXISTS (SELECT 1 FROM platform_admins WHERE user_id = %s)",
            (user_id, user_id),
        ).fetchone()
        if has_access is None:
            raise forbidden(
                NO_ACCESS
            )  # mesma resposta de "conta desconhecida": não revela quem existe
        conn.execute("UPDATE users SET name = %s WHERE id = %s", (identity.name or "", user_id))
        conn.execute(
            "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
            (user_id, hash_token(token), expires),
        )
        # Sessões esquecidas não ficam valendo para sempre: só as mais recentes seguem ativas.
        conn.execute(
            "UPDATE sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL "
            "AND id NOT IN (SELECT id FROM sessions WHERE user_id = %s AND revoked_at IS NULL "
            "ORDER BY created_at DESC LIMIT %s)",
            (user_id, user_id, MAX_SESSIONS),
        )
    return token


def revoke_all_sessions(db: Database, user_id: uuid.UUID) -> int:
    """ "Sair de todos os aparelhos": encerra toda sessão ativa da pessoa."""
    with db.tx(user_id=user_id) as conn:
        return conn.execute(
            "UPDATE sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL",
            (user_id,),
        ).rowcount


def revoke_session(db: Database, token: str) -> None:
    with db.tx(session_hash=hash_token(token)) as conn:
        row = conn.execute(
            "SELECT id, user_id FROM sessions WHERE token_hash = %s", (hash_token(token),)
        ).fetchone()
        if row is None:
            return
        db.set_user(conn, row["user_id"])
        conn.execute("UPDATE sessions SET revoked_at = now() WHERE id = %s", (row["id"],))


def resolve_principal(db: Database, token: str | None, requested_tenant: str | None) -> Principal:
    if not token:
        raise unauthorized()
    with db.tx(session_hash=hash_token(token)) as conn:
        session = conn.execute(
            "SELECT user_id FROM sessions "
            "WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > now()",
            (hash_token(token),),
        ).fetchone()
        if session is None:
            raise unauthorized("Sua sessão expirou. Entre novamente.")
        user_id: uuid.UUID = session["user_id"]
        db.set_user(conn, user_id)
        user = conn.execute("SELECT email, name FROM users WHERE id = %s", (user_id,)).fetchone()
        rows = conn.execute(
            "SELECT m.tenant_id, t.name AS tenant_name, m.role "
            "FROM memberships m JOIN tenants t ON t.id = m.tenant_id "
            "WHERE m.user_id = %s AND t.status = 'active' ORDER BY m.created_at",
            (user_id,),
        ).fetchall()
    if user is None or not rows:
        raise forbidden("Sua conta não tem acesso ativo a nenhum cliente.")
    memberships = tuple(Membership(r["tenant_id"], r["tenant_name"], r["role"]) for r in rows)
    chosen = memberships[0]
    if requested_tenant:
        try:
            wanted = uuid.UUID(requested_tenant)
        except ValueError as exc:
            raise bad_request("invalid_tenant", "Identificador de cliente inválido.") from exc
        match = next((m for m in memberships if m.tenant_id == wanted), None)
        if match is None:
            raise forbidden("Você não pertence a este cliente.")
        chosen = match
    return Principal(
        user_id=user_id,
        email=user["email"],
        name=user["name"],
        memberships=memberships,
        tenant_id=chosen.tenant_id,
        role=chosen.role,
    )


def tenant_features(db: Database, principal: Principal) -> tuple[str, str | None, list[str]]:
    """Devolve (status, plano, recursos). Plano suspenso, cancelado, reembolsado ou ausente não
    libera nada; em atraso dentro da carência libera tudo."""
    with db.tx(tenant_id=principal.tenant_id, user_id=principal.user_id) as conn:
        row = conn.execute(
            "SELECT tp.status, tp.plan_key, p.features FROM tenant_plans tp "
            "JOIN plans p ON p.key = tp.plan_key WHERE tp.tenant_id = %s",
            (principal.tenant_id,),
        ).fetchone()
    if row is None:
        return ("none", None, [])
    blocked = row["status"] in ("suspended", "canceled", "refunded")
    features: list[str] = [] if blocked else list(row["features"])
    return (row["status"], row["plan_key"], features)


# ---------------------------------------------------------------- conexões


def _public_connection(row: dict[str, Any], provider: Provider, base_url: str) -> dict[str, Any]:
    hint: dict[str, Any] = dict(row["config_hint"])
    out: dict[str, Any] = {
        "provider": row["provider"],
        "status": row["status"],
        "last_verified_at": row["last_verified_at"],
        "last_error": row["last_error"],
        "values": hint,
    }
    if provider.webhook:
        out["webhook_url"] = f"{base_url}/v1/webhooks/{provider.key}/{row['public_id']}"
    return out


class ConnectionService:
    def __init__(
        self, db: Database, settings: Settings, box: SecretBox, tester: ConnectionTester
    ) -> None:
        self._db = db
        self._settings = settings
        self._box = box
        self._tester = tester

    def _require_feature(self, provider: Provider, features: list[str]) -> None:
        if provider.feature not in features:
            raise AppError(
                402,
                "plan_required",
                f"Seu plano não inclui {provider.name}. Faça upgrade para usar este recurso.",
            )

    def list(self, p: Principal) -> list[dict[str, Any]]:
        with self._db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
            rows = conn.execute(
                "SELECT * FROM connections WHERE tenant_id = %s ORDER BY created_at",
                (p.tenant_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            provider = get_provider(row["provider"])
            if provider is not None:
                result.append(_public_connection(row, provider, self._settings.public_base_url))
        return result

    def put(self, p: Principal, provider_key: str, values: dict[str, str]) -> dict[str, Any]:
        if p.role not in EDIT_ROLES:
            raise forbidden("Só dono ou administrador altera conexões.")
        provider = get_provider(provider_key)
        if provider is None:
            raise not_found("Provedor desconhecido.")
        _, _, features = tenant_features(self._db, p)
        self._require_feature(provider, features)

        allowed = {f.key for f in provider.fields}
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise bad_request("unknown_field", "Campos não reconhecidos: " + ", ".join(unknown))

        tenant = str(p.tenant_id)
        with self._db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
            current = conn.execute(
                "SELECT * FROM connections WHERE tenant_id = %s AND provider = %s FOR UPDATE",
                (p.tenant_id, provider.key),
            ).fetchone()
            config: dict[str, Any] = {}
            if current is not None:
                try:
                    config = self._box.decrypt(
                        current["config_encrypted"], tenant_id=tenant, provider=provider.key
                    )
                except CryptoError as exc:
                    raise AppError(
                        500, "credentials_unreadable", "Não foi possível ler a credencial salva."
                    ) from exc
            changed: list[str] = []
            for f in provider.fields:
                incoming = values.get(f.key)
                if incoming is None or incoming == "":
                    continue  # em branco mantém o valor atual (segredos nunca voltam ao navegador)
                if config.get(f.key) != incoming:
                    config[f.key] = incoming.strip()
                    changed.append(f.key)
            missing = [f.label for f in provider.fields if f.required and not config.get(f.key)]
            if missing:
                raise bad_request("missing_fields", "Preencha: " + ", ".join(missing))

            new_secret: str | None = None
            if provider.webhook and provider.generates_secret and not config.get("webhook_secret"):
                new_secret = secrets.token_urlsafe(24)
                config["webhook_secret"] = new_secret
                changed.append("webhook_secret")

            hint: dict[str, Any] = {}
            for f in provider.fields:
                raw = str(config.get(f.key, ""))
                hint[f.key] = (mask_secret(raw) if f.secret else raw) if raw else ""
            if provider.webhook and provider.generates_secret:
                hint["webhook_secret"] = mask_secret(str(config["webhook_secret"]))

            blob = self._box.encrypt(config, tenant_id=tenant, provider=provider.key)
            if current is None:
                row = conn.execute(
                    "INSERT INTO connections (tenant_id, provider, public_id, config_encrypted, "
                    "config_hint) VALUES (%s, %s, %s, %s, %s::jsonb) RETURNING *",
                    (
                        p.tenant_id,
                        provider.key,
                        secrets.token_urlsafe(12),
                        blob,
                        json.dumps(hint),
                    ),
                ).fetchone()
            else:
                row = conn.execute(
                    "UPDATE connections SET config_encrypted = %s, config_hint = %s::jsonb, "
                    "status = 'pending', last_error = NULL, updated_at = now() "
                    "WHERE id = %s RETURNING *",
                    (blob, json.dumps(hint), current["id"]),
                ).fetchone()
            assert row is not None
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="connection.saved",
                target=provider.key,
                detail={"fields_changed": changed},
            )
        out = _public_connection(row, provider, self._settings.public_base_url)
        if new_secret is not None:
            out["webhook_secret_once"] = new_secret
        return out

    def test(self, p: Principal, provider_key: str) -> dict[str, Any]:
        if p.role not in EDIT_ROLES:
            raise forbidden("Só dono ou administrador testa conexões.")
        provider = get_provider(provider_key)
        if provider is None:
            raise not_found("Provedor desconhecido.")
        tenant = str(p.tenant_id)
        with self._db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
            current = conn.execute(
                "SELECT * FROM connections WHERE tenant_id = %s AND provider = %s FOR UPDATE",
                (p.tenant_id, provider.key),
            ).fetchone()
            if current is None:
                raise not_found("Esta conexão ainda não foi configurada.")
            config = self._box.decrypt(
                current["config_encrypted"], tenant_id=tenant, provider=provider.key
            )
            if provider.group == "checkout":
                # Cakto/Hotmart não têm como serem consultadas por nós: a prova é um evento
                # válido chegando (ver events/ingest.py). O botão apenas informa esse estado e
                # nunca apaga uma confirmação que já veio de um evento real.
                if current["status"] == "connected" and current["last_verified_at"] is not None:
                    message = f"Confirmada: já chegou evento válido da {provider.name}."
                else:
                    message = (
                        f"Ainda não chegou nenhum evento válido da {provider.name}. Cadastre o "
                        "endereço do webhook lá e envie o evento de teste: a conexão é "
                        "confirmada quando ele chegar."
                    )
                audit(
                    conn,
                    tenant_id=p.tenant_id,
                    actor=p.user_id,
                    action="connection.tested",
                    target=provider.key,
                    detail={"ok": current["status"] == "connected", "by": "webhook"},
                )
                out = _public_connection(current, provider, self._settings.public_base_url)
                out["message"] = message
                return out
            result = self._tester.test(provider, {k: str(v) for k, v in config.items()})
            row = conn.execute(
                "UPDATE connections SET status = %s, last_error = %s, "
                "last_verified_at = CASE WHEN %s THEN now() ELSE last_verified_at END, "
                "updated_at = now() WHERE id = %s RETURNING *",
                (
                    "connected" if result.ok else "needs_attention",
                    None if result.ok else result.message,
                    result.ok,
                    current["id"],
                ),
            ).fetchone()
            assert row is not None
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="connection.tested",
                target=provider.key,
                detail={"ok": result.ok},
            )
        out = _public_connection(row, provider, self._settings.public_base_url)
        out["message"] = result.message
        return out

    def delete(self, p: Principal, provider_key: str) -> None:
        if p.role not in EDIT_ROLES:
            raise forbidden("Só dono ou administrador remove conexões.")
        provider = get_provider(provider_key)
        if provider is None:
            raise not_found("Provedor desconhecido.")
        with self._db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
            deleted = conn.execute(
                "DELETE FROM connections WHERE tenant_id = %s AND provider = %s RETURNING id",
                (p.tenant_id, provider.key),
            ).fetchone()
            if deleted is None:
                raise not_found("Esta conexão não existe.")
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="connection.removed",
                target=provider.key,
            )
