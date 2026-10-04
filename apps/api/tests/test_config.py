from __future__ import annotations

import pytest

from fm_seller.config import Settings
from fm_seller.security.crypto import SecretBox

GOOD = {
    "env": "prod",
    "database_url": "postgresql://fm_app:x9@db.internal:5432/app",
    "database_admin_url": "postgresql://fm_owner:y8@db.internal:5432/app",
    "secrets_keys": SecretBox.generate_key_spec(),
    "google_client_id": "abc.apps.googleusercontent.com",
    "cookie_secure": True,
    "web_origin": "https://app.example.test",
    "public_base_url": "https://api.example.test",
}


def test_prod_with_real_config_is_accepted() -> None:
    assert Settings(**GOOD).is_production_like  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "override",
    [
        {"secrets_keys": ""},
        {"google_client_id": ""},
        {"cookie_secure": False},
        {"web_origin": "http://app.example.test"},
        {"database_url": "postgresql://fm_app:dev_app_only@localhost:5432/fm_dev"},
    ],
)
def test_prod_rejects_insecure_defaults(override: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="Configuração insegura"):
        Settings(**{**GOOD, **override})  # type: ignore[arg-type]


def test_dev_keeps_local_defaults() -> None:
    assert Settings(env="dev").cookie_secure is False


def test_whatsapp_live_is_off_by_default() -> None:
    from fm_seller.config import Settings

    assert Settings().whatsapp_live is False
