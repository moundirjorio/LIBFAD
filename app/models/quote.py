from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Quote(Base, TenantMixin, TimestampMixin):
    """Devis."""

    __tablename__ = "quotes"
    __table_args__ = (UniqueConstraint("tenant_id", "number", name="uq_quotes_tenant_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)

    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # draft | sent | accepted | refused | expired | converted

    issue_date: Mapped[date] = mapped_column(Date, default=date.today, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    accepted_at: Mapped[date | None] = mapped_column(Date)
    # use_alter : la FK est ajoutée via ALTER après création des deux tables
    # (rompt le cycle circulaire quotes <-> invoices pour create_all et Alembic).
    converted_invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL", use_alter=True, name="fk_quotes_converted_invoice_id")
    )

    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    locale: Mapped[str] = mapped_column(String(10), default="fr_FR")
    bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_accounts.id", ondelete="SET NULL"))

    subtotal_ht: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    total_discount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    total_tax: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    total_ttc: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    notes_public: Mapped[str | None] = mapped_column(Text)
    notes_internal: Mapped[str | None] = mapped_column(Text)
    footer: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(String(500))

    items: Mapped[list["QuoteItem"]] = relationship(
        back_populates="quote", cascade="all, delete-orphan", order_by="QuoteItem.position"
    )


class QuoteItem(Base, TenantMixin, TimestampMixin):
    __tablename__ = "quote_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))

    position: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(14, 4), default=1)
    unit: Mapped[str] = mapped_column(String(20), default="u")
    unit_price: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    discount_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    tax_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=0)

    line_subtotal_ht: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    line_tax: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    line_total_ttc: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    quote: Mapped["Quote"] = relationship(back_populates="items")
