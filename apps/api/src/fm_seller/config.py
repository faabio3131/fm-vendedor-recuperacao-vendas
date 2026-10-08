"""Configuração por ambiente. Nenhum segredo fica no código."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_WEBHOOK_SECRET_CHARS = 32


def parse_webhook_secrets(raw: str) -> dict[str, str]:
    """`kid:segredo,kid2:segredo2` -> {kid: segredo}. Cada segredo precisa de 32+ caracteres."""
    out: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        kid, sep, secret = part.partition(":")
        if not sep or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", kid):
            raise ValueError("FM_FMCOMMAND_WEBHOOK_SECRETS: use kid:segredo (kid simples)")
        if len(secret) < MIN_WEBHOOK_SECRET_CHARS:
            raise ValueError("FM_FMCOMMAND_WEBHOOK_SECRETS: cada segredo precisa de 32+ caracteres")
        if kid in out:
            raise ValueError("FM_FMCOMMAND_WEBHOOK_SECRETS: kid repetido")
        out[kid] = secret
    return out


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

    # Billing Central do FM Command (ADR-0005, contrato fmcc.license.v1). TUDO desligado por padrão:
    # `off` não recebe nem consulta nada; `shadow` valida e registra sem alterar licença real;
    # `enforce` aplica a licença do Command (só depois da aprovação humana registrada).
    fmcommand_mode: Literal["off", "shadow", "enforce"] = "off"
    # Código canônico do produto no catálogo do Command (nunca inventado aqui).
    fmcommand_product_code: str = ""
    # Segredos de assinatura dos eventos: "kid:segredo,kid2:segredo2" (dois valem na rotação).
    fmcommand_webhook_secrets: str = ""
    # API de licenças do Command (somente leitura): base https e token de serviço.
    fmcommand_api_base_url: str = ""
    fmcommand_api_token: SecretStr = SecretStr("")
    fmcommand_reconcile_minutes: int = Field(default=15, ge=1, le=60)
    fmcommand_api_timeout_seconds: float = Field(default=10.0, ge=1, le=30)
    # Contingência: máximo 72 h (teto fixo, aprovado). Não renova sozinha.
    fmcommand_contingency_hours: int = Field(default=72, ge=1, le=72)
    # Sem reconciliação bem-sucedida por este tempo, o ops-check avisa.
    fmcommand_stale_after_minutes: int = Field(default=45, ge=5, le=1440)

    @model_validator(mode="after")
    def _control_plane_token_is_strong(self) -> Settings:
        if self.fmcc_control_plane_token and len(self.fmcc_control_plane_token) < 32:
            raise ValueError("FM_FMCC_CONTROL_PLANE_TOKEN precisa ter ao menos 32 caracteres")
        return self

    @model_validator(mode="after")
    def _fmcommand_config_is_safe(self) -> Settings:
        """Config do Billing Central: nada pela metade. Desligado não exige nada."""
        if self.fmcommand_mode == "off":
            return self
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", self.fmcommand_product_code):
            raise ValueError("FM_FMCOMMAND_PRODUCT_CODE precisa ser o código canônico do catálogo")
        if self.fmcommand_webhook_secrets:
            parse_webhook_secrets(self.fmcommand_webhook_secrets)
        if self.fmcommand_api_base_url:
            parsed = urlsplit(self.fmcommand_api_base_url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("FM_FMCOMMAND_API_BASE_URL precisa ser https, sem credencial")
            if len(self.fmcommand_api_token.get_secret_value()) < 32:
                raise ValueError("FM_FMCOMMAND_API_TOKEN precisa ter ao menos 32 caracteres")
        if self.fmcommand_mode == "enforce" and not (
            self.fmcommand_webhook_secrets and self.fmcommand_api_base_url
        ):
            raise ValueError(
                "FM_FMCOMMAND_MODE=enforce exige segredos de assinatura e a API de licenças"
            )
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
