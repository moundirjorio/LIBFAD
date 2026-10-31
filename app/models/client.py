from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Client(Base, TenantMixin, TimestampMixin):
    """Client / Compte (entreprise ou particulier)."""

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Type & identité
    client_type: Mapped[str] = mapped_column(String(20), default="company")  # company|individual
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(50), index=True)  # référence interne

    # Identifiants légaux
    siret: Mapped[str | None] = mapped_column(String(50))
    vat_number: Mapped[str | None] = mapped_column(String(50))
    ice: Mapped[str | None] = mapped_column(String(50))

    # Contact principal
    email: Mapped[str | None] = mapped_column(String(200), index=True)
    phone: Mapped[str | None] = mapped_column(String(50))
    website: Mapped[str | None] = mapped_column(String(200))

    # Adresse facturation
    billing_address_line1: Mapped[str | None] = mapped_column(String(200))
    billing_address_line2: Mapped[str | None] = mapped_column(String(200))
    billing_postal_code: Mapped[str | None] = mapped_column(String(20))
    billing_city: Mapped[str | None] = mapped_column(String(100))
    billing_country: Mapped[str | None] = mapped_column(String(2), default="FR")

    # Adresse livraison
    shipping_address_line1: Mapped[str | None] = mapped_column(String(200))
    shipping_address_line2: Mapped[str | None] = mapped_column(String(200))
    shipping_postal_code: Mapped[str | None] = mapped_column(String(20))
    shipping_city: Mapped[str | None] = mapped_column(String(100))
    shipping_country: Mapped[str | None] = mapped_column(String(2))

    # Commercial
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    locale: Mapped[str] = mapped_column(String(10), default="fr_FR")
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)  # délai de paiement
    payment_method: Mapped[str | None] = mapped_column(String(40))  # transfer|card|cash|check
    discount_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=0)

    # Risque / scoring
    credit_limit: Mapped[float | None] = mapped_column(Numeric(14, 2))
    solvency_score: Mapped[int | None] = mapped_column(Integer)  # 0-100 calculé par IA
    risk_level: Mapped[str] = mapped_column(String(20), default="unknown")  # low|medium|high|unknown

    # Notes
    notes: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(String(500))  # CSV simple

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    contacts: Mapped[list["Contact"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )


class Contact(Base, TenantMixin, TimestampMixin):
    """Personne de contact chez un client."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)

    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str | None] = mapped_column(String(100))  # fonction
    email: Mapped[str | None] = mapped_column(String(200), index=True)
    phone: Mapped[str | None] = mapped_column(String(50))
    mobile: Mapped[str | None] = mapped_column(String(50))

    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    receives_invoices: Mapped[bool] = mapped_column(Boolean, default=True)
    receives_dunning: Mapped[bool] = mapped_column(Boolean, default=True)

    notes: Mapped[str | None] = mapped_column(Text)

    client: Mapped["Client"] = relationship(back_populates="contacts")
