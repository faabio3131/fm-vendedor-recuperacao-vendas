"""Pareamento Evolution no boundary canônico de conexões, sem gravar QR/segredo no banco."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fm_seller.channels.evolution import EvolutionClient, InstanceMissing
from fm_seller.config import Settings
from fm_seller.db import Database
from fm_seller.errors import AppError, forbidden
from fm_seller.security.crypto import CryptoError, SecretBox
from fm_seller.services import EDIT_ROLES, Principal, audit, tenant_features

_PROVIDER = "evolution"
_TTL_SECONDS = 30  # limite local conservador; a Evolution pode expirar antes


class EvolutionPairingService:
    def __init__(
        self,
        db: Database,
        box: SecretBox,
        settings: Settings,
        client: EvolutionClient | None = None,
    ) -> None:
        self._db = db
        self._box = box
        self._settings = settings
        self.client = client or EvolutionClient(
            base_url=settings.evolution_api_base_url,
            api_key=settings.evolution_api_key.get_secret_value(),
            timeout=settings.evolution_timeout_seconds,
        )

    def _authorize(self, p: Principal) -> None:
        if p.role not in EDIT_ROLES:
            raise forbidden("Somente dono ou administrador pode parear WhatsApp.")
        _, _, features = tenant_features(self._db, p)
        if "channel.whatsapp" not in features:
            raise AppError(402, "plan_required", "Seu plano não permite WhatsApp.")

    def _enabled(self) -> None:
        if not self._settings.evolution_enabled:
            raise AppError(
                503, "evolution_disabled", "Pareamento Evolution ainda não foi liberado."
            )

    def _instance_name(self, p: Principal) -> str:
        # Nome estável, sem input do navegador, separado por tenant.
        return "fmav-" + hashlib.sha256(p.tenant_id.bytes).hexdigest()[:32]

    def _bound_instance(self, p: Principal) -> str | None:
        with self._db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
            row = conn.execute(
                "SELECT config_encrypted FROM connections WHERE tenant_id = %s AND provider = %s",
                (p.tenant_id, _PROVIDER),
            ).fetchone()
        if row is None:
            return None
        try:
            config = self._box.decrypt(
                row["config_encrypted"], tenant_id=str(p.tenant_id), provider=_PROVIDER
            )
        except CryptoError:
            raise AppError(
                503, "instance_binding_invalid", "Vínculo de instância inválido."
            ) from None
        name = config.get("instance_name")
        if name != self._instance_name(p):
            raise AppError(
                409, "instance_binding_mismatch", "A instância deste cliente não confere."
            )
        return str(name)

    def _reserve(self, p: Principal) -> str:
        name = self._bound_instance(p)
        if name is not None:
            return name
        name = self._instance_name(p)
        blob = self._box.encrypt(
            {"instance_name": name}, tenant_id=str(p.tenant_id), provider=_PROVIDER
        )
        with self._db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
            conn.execute(
                "INSERT INTO connections (tenant_id, provider, public_id, config_encrypted, "
                "config_hint, status) VALUES (%s, %s, %s, %s, %s::jsonb, 'pending') "
                "ON CONFLICT (tenant_id, provider) DO NOTHING",
                (p.tenant_id, _PROVIDER, name, blob, json.dumps({})),
            )
            audit(
                conn,
                tenant_id=p.tenant_id,
                actor=p.user_id,
                action="evolution.pairing.requested",
                target=_PROVIDER,
            )
        # Releitura sempre escopada: evita assumir ownership após corrida.
        bound = self._bound_instance(p)
        if bound != name:
            raise AppError(409, "instance_binding_mismatch", "Instância não vinculada.")
        return name

    def _set_status(self, p: Principal, status: str, verified: bool = False) -> None:
        with self._db.tx(tenant_id=p.tenant_id, user_id=p.user_id) as conn:
            previous = conn.execute(
                "SELECT status FROM connections WHERE tenant_id = %s AND provider = %s FOR UPDATE",
                (p.tenant_id, _PROVIDER),
            ).fetchone()
            conn.execute(
                "UPDATE connections SET status = %s, last_error = NULL, "
                "last_verified_at = CASE WHEN %s THEN now() ELSE NULL END, "
                "updated_at = now() WHERE tenant_id = %s AND provider = %s",
                (status, verified, p.tenant_id, _PROVIDER),
            )
            if verified and previous is not None and previous["status"] != "connected":
                audit(
                    conn,
                    tenant_id=p.tenant_id,
                    actor=p.user_id,
                    action="evolution.session.verified",
                    target=_PROVIDER,
                )

    def status(self, p: Principal) -> dict[str, Any]:
        self._authorize(p)
        if not self._settings.evolution_enabled:
            return {"status": "unavailable", "connected": False}
        name = self._bound_instance(p)
        if name is None:
            return {"status": "not_configured", "connected": False}
        try:
            state = self.client.state(name)
        except InstanceMissing:
            self._set_status(p, "disconnected")
            return {"status": "disconnected", "connected": False}
        except AppError:
            self._set_status(p, "needs_attention")
            raise
        connected = state == "open"
        result = (
            "connected" if connected else "pending" if state == "connecting" else "disconnected"
        )
        self._set_status(p, result, verified=connected)
        return {"status": result, "connected": connected}

    def connect(self, p: Principal) -> dict[str, Any]:
        self._authorize(p)
        self._enabled()
        name = self._reserve(p)
        try:
            try:
                state = self.client.state(name)
            except InstanceMissing:
                self.client.create(name)
                state = "connecting"
            if state == "open":
                self._set_status(p, "connected", verified=True)
                return {"status": "connected", "connected": True}
            qr_code = self.client.qr(name)
        except InstanceMissing:
            self._set_status(p, "needs_attention")
            raise AppError(
                503, "evolution_instance_not_ready", "Instância ainda não pronta para o QR."
            ) from None
        except AppError:
            self._set_status(p, "needs_attention")
            raise
        self._set_status(p, "pending")
        return {
            "status": "pending",
            "connected": False,
            "qr_code": qr_code,
            "expires_in_seconds": _TTL_SECONDS,
        }
