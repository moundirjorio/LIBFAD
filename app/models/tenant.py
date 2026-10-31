from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Tenant(Base, TimestampMixin):
    """Organisation / entreprise utilisant LIBFAD."""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)

    # Identité légale
    legal_name: Mapped[str | None] = mapped_column(String(200))
    siret: Mapped[str | None] = mapped_column(String(50))
    vat_number: Mapped[str | None] = mapped_column(String(50))
    ice: Mapped[str | None] = mapped_column(String(50))  # Maroc
    rc: Mapped[str | None] = mapped_column(String(50))  # Maroc

    # Coordonnées
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))
    website: Mapped[str | None] = mapped_column(String(200))

    # Adresse
    address_line1: Mapped[str | None] = mapped_column(String(200))
    address_line2: Mapped[str | None] = mapped_column(String(200))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    city: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(2), default="FR")

    # Configuration
    locale: Mapped[str] = mapped_column(String(10), default="fr_FR")
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Paris")

    # Branding
    logo_path: Mapped[str | None] = mapped_column(String(300))
    primary_color: Mapped[str | None] = mapped_column(String(7), default="#0F172A")
    invoice_footer: Mapped[str | None] = mapped_column(String(1000))
    # Modèle de PDF facture/devis : "classic" | "modern"
    pdf_template: Mapped[str] = mapped_column(String(20), default="classic")

    # Numérotation des documents. Placeholders : {YYYY} année, {YY} année 2 chiffres,
    # {MM} mois, {seq} compteur (rempli à `number_padding` chiffres, ex. 00010).
    invoice_number_format: Mapped[str] = mapped_column(String(60), default="FA-{YYYY}-{seq}")
    quote_number_format: Mapped[str] = mapped_column(String(60), default="DV-{YYYY}-{seq}")
    number_padding: Mapped[int] = mapped_column(Integer, default=5)
    number_reset_yearly: Mapped[bool] = mapped_column(Boolean, default=True)

    # Bancaire
    bank_name: Mapped[str | None] = mapped_column(String(200))
    iban: Mapped[str | None] = mapped_column(String(50))
    bic: Mapped[str | None] = mapped_column(String(20))
    # Compte bancaire (trésorerie) pré-sélectionné par défaut sur factures/devis/achats
    default_bank_account_id: Mapped[int | None] = mapped_column(Integer)

    # Bot Telegram de la société (jeton fourni par @BotFather)
    telegram_bot_token: Mapped[str | None] = mapped_column(String(100), unique=True)
    telegram_bot_username: Mapped[str | None] = mapped_column(String(64))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Abonnement SaaS (facturation de LIBFAD à ses clients)
    plan: Mapped[str] = mapped_column(String(20), default="free")  # free | pro | business
    plan_status: Mapped[str] = mapped_column(String(20), default="trialing")  # trialing | active | past_due | canceled
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stripe_customer_id: Mapped[str | None] = mapped_column(String(100))
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(100))

    users: Mapped[list["User"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")  # noqa: F821
