"""Configuração por ambiente. Nenhum segredo fica no código."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
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

    session_ttl_hours: int = Field(default=24 * 14, ge=1)
    cookie_secure: bool = False

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
