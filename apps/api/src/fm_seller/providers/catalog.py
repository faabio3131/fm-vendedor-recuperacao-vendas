"""Catálogo de provedores: o que cada conexão pede ao cliente. É dado, não tela fixa.

A interface desenha o formulário a partir deste catálogo; um provedor novo entra aqui
(e ganha um adaptador), sem mexer em nada específico de cliente.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    secret: bool = False
    required: bool = True
    help: str = ""


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    group: str  # canal | checkout | pagamento | ia | anuncios
    phase: int
    feature: str
    fields: tuple[Field, ...]
    webhook: bool = (
        False  # o sistema gera URL e segredo de webhook para o cliente colar na plataforma
    )
    description: str = ""
    extra: dict[str, str] = field(default_factory=dict)


PROVIDERS: tuple[Provider, ...] = (
    Provider(
        key="whatsapp_cloud",
        name="WhatsApp oficial",
        group="canal",
        phase=1,
        feature="channel.whatsapp",
        description="API oficial do WhatsApp Business (Meta).",
        fields=(
            Field("phone_number_id", "ID do número de telefone"),
            Field("waba_id", "ID da conta do WhatsApp Business"),
            Field("access_token", "Token de acesso", secret=True),
        ),
        webhook=True,
    ),
    Provider(
        key="messenger",
        name="Facebook Messenger",
        group="canal",
        phase=1,
        feature="channel.messenger",
        description="Mensagens da sua página do Facebook.",
        fields=(
            Field("page_id", "ID da página"),
            Field("page_access_token", "Token de acesso da página", secret=True),
        ),
        webhook=True,
    ),
    Provider(
        key="instagram_dm",
        name="Instagram (mensagens diretas)",
        group="canal",
        phase=1,
        feature="channel.instagram",
        description="Mensagens diretas da conta profissional do Instagram.",
        fields=(
            Field("instagram_account_id", "ID da conta do Instagram"),
            Field("access_token", "Token de acesso", secret=True),
        ),
        webhook=True,
    ),
    Provider(
        key="cakto",
        name="Cakto",
        group="checkout",
        phase=1,
        feature="checkout.cakto",
        description="Eventos de checkout e compra da Cakto.",
        fields=(Field("api_token", "Token de API (opcional)", secret=True, required=False),),
        webhook=True,
    ),
    Provider(
        key="hotmart",
        name="Hotmart",
        group="checkout",
        phase=1,
        feature="checkout.hotmart",
        description="Eventos de checkout e compra da Hotmart.",
        fields=(Field("hottok", "Token de verificação (hottok)", secret=True),),
        webhook=True,
    ),
    Provider(
        key="ai_model",
        name="Modelo de IA próprio",
        group="ia",
        phase=1,
        feature="ai.custom_key",
        description="Opcional. Sem isso, o vendedor usa o modelo da plataforma.",
        fields=(
            Field("provider_name", "Provedor do modelo"),
            Field("api_key", "Chave de API", secret=True),
        ),
    ),
    Provider(
        key="payment_gateway",
        name="Provedor de pagamento",
        group="pagamento",
        phase=2,
        feature="billing.gateway",
        description="Gera cobranças em Pix, boleto e cartão na sua conta.",
        fields=(
            Field("gateway", "Provedor de pagamento"),
            Field("api_key", "Chave de API", secret=True),
        ),
        webhook=True,
    ),
    Provider(
        key="meta_ads",
        name="Meta Ads",
        group="anuncios",
        phase=3,
        feature="ads.meta",
        description="Conta de anúncios da Meta.",
        fields=(
            Field("ad_account_id", "ID da conta de anúncios"),
            Field("access_token", "Token de acesso", secret=True),
        ),
    ),
    Provider(
        key="google_ads",
        name="Google Ads",
        group="anuncios",
        phase=4,
        feature="ads.google",
        description="Conta do Google Ads autorizada com login Google.",
        fields=(
            Field("customer_id", "ID do cliente do Google Ads"),
            Field("refresh_token", "Token de atualização", secret=True),
        ),
    ),
)

_BY_KEY = {p.key: p for p in PROVIDERS}


def get_provider(key: str) -> Provider | None:
    return _BY_KEY.get(key)


def mask_secret(value: str) -> str:
    """Mostra só o final do segredo. Valores curtos viram apenas pontos."""
    return "••••" + value[-4:] if len(value) >= 12 else "••••"
