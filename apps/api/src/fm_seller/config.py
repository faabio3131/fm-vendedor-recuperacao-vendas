"""Configuração por ambiente. Nenhum segredo fica no código."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FM_", env_file=None, extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"

    # Conexão do app: papel SEM bypass de RLS.
    database_url: str = "postgresql://fm_app:dev_app_only@localhost:5432/fm_dev"
    # Conexão de migrations: dono das tabelas. Usada só pelo runner de migrations e pela CLI.
    database_admin_url: str = "postgresql://fm_owner:dev_owner_only@localhost:5432/fm_dev"

    # Chaves de cifragem de credenciais: "id:base64,id2:base64". A primeira é a ativa.
    secrets_keys: str = ""

    google_client_id: str = ""
    web_origin: str = "http://localhost:3000"
    public_base_url: str = "http://localhost:8000"

    # Segredos dos webhooks das compras do PRÓPRIO SaaS (Cakto/Hotmart da F&M). Vazio = desligado.
    platform_cakto_secret: str = ""
    platform_hotmart_hottok: str = ""

    # Modelo de IA do vendedor (chave da plataforma). Vazio: dev usa simulador; staging/prod
    # deixam as conversas com uma pessoa. Em `test` o simulador é sempre usado.
    ai_api_key: SecretStr = SecretStr("")
    ai_model: str = "gemini-3.8-flash"
    ai_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    ai_thinking_level: Literal["low", "medium", "high"] = "low"
    ai_timeout_seconds: float = Field(default=20.0, ge=1, le=60)

    # Envio real pelo WhatsApp Cloud API. Desligado por padrão: só ligar depois de validar com uma
    # conta real (docs/PENDENCIAS_EXTERNAS.md). Em FM_ENV=test nunca é usado.
    whatsapp_live: bool = False
    meta_graph_base: str = "https://graph.facebook.com"
    meta_graph_version: str = "v26.0"
    meta_timeout_seconds: float = Field(default=15.0, ge=1, le=60)

    # Captura de eventos reais de checkout para conferir o formato (docs/OPERACAO.md). Desligada por
    # padrão; guarda o corpo cifrado, sem segredos, e apaga sozinha depois do prazo.
    capture_events: bool = False
    capture_ttl_hours: int = Field(default=72, ge=1, le=720)
    # Dias entre o pedido de exclusão da conta e o apagamento de tudo (o dono pode cancelar antes).
    tenant_deletion_grace_days: int = Field(default=30, ge=0, le=365)
    capture_keep: int = Field(default=100, ge=1, le=1000)

    # Primeira subida sem terminal (docs/STAGING_RENDER.md): `cli bootstrap`. Tudo opcional.
    bootstrap_app_password: SecretStr = SecretStr("")
    bootstrap_owner_email: str = ""
    bootstrap_tenant_name: str = ""

    session_ttl_hours: int = Field(default=24 * 14, ge=1)
    cookie_secure: bool = False
    # Limites contra abuso (docs/SEGURANCA.md). Contagem na memória do processo: com mais de uma
    # instância cada uma conta sozinha. Em teste e no e2e local fica desligado de propósito.
    rate_limit_enabled: bool = True
    # Atrás do proxy do painel/Render o IP do cliente vem em X-Forwarded-For (última entrada).
    # NÃO CONFIRMADO no Render: sem isto o limite por IP enxerga só o proxy.
    trust_proxy: bool = False
    max_body_bytes: int = Field(default=1_048_576, ge=1024)
    rate_login_per_5min: int = Field(default=60, ge=1)
    rate_api_per_min: int = Field(default=1200, ge=1)
    rate_sensitive_per_10min: int = Field(default=20, ge=1)
    rate_webhook_per_min: int = Field(default=1200, ge=1)
    rate_webhook_fail_per_10min: int = Field(default=30, ge=1)
    # Conexão com o FM Command (docs/FM_COMMAND_INTEGRACAO.md). Vazio = desligada (rotas dão 404).
    # Se preenchido, precisa ter ao menos 32 caracteres; valor aleatório próprio, nunca no git.
    fmcc_control_plane_token: str = ""

    @model_validator(mode="after")
    def _control_plane_token_is_strong(self) -> Settings:
        if self.fmcc_control_plane_token and len(self.fmcc_control_plane_token) < 32:
            raise ValueError("FM_FMCC_CONTROL_PLANE_TOKEN precisa ter ao menos 32 caracteres")
        return self

    @model_validator(mode="after")
    def _require_real_config_outside_dev(self) -> Settings:
        """Em staging/produção nada pode cair em padrão de desenvolvimento."""
        if not self.is_production_like:
            return self
        problems: list[str] = []
        if not self.secrets_keys:
            problems.append("FM_SECRETS_KEYS")
        if not self.google_client_id:
            problems.append("FM_GOOGLE_CLIENT_ID")
        if not self.cookie_secure:
            problems.append("FM_COOKIE_SECURE=true")
        if not self.web_origin.startswith("https://"):
            problems.append("FM_WEB_ORIGIN (https)")
        if not self.public_base_url.startswith("https://"):
            problems.append("FM_PUBLIC_BASE_URL (https)")
        for name, url in (
            ("FM_DATABASE_URL", self.database_url),
            ("FM_DATABASE_ADMIN_URL", self.database_admin_url),
        ):
            if "dev_" in url or "@localhost" in url:
                problems.append(name)
        if problems:
            raise ValueError("Configuração insegura para " + self.env + ": " + ", ".join(problems))
        return self

    @property
    def is_production_like(self) -> bool:
        return self.env in ("staging", "prod")


@lru_cache
def get_settings() -> Settings:
    return Settings()
