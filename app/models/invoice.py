from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Invoice(Base, TenantMixin, TimestampMixin):
    """Facture émise au client."""

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number", name="uq_invoices_tenant_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id", ondelete="SET NULL"))
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"))

    # Statut
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # draft | issued | sent | partially_paid | paid | overdue | cancelled | refunded

    # Dates
    issue_date: Mapped[date] = mapped_column(Date, default=date.today, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    paid_at: Mapped[date | None] = mapped_column(Date)
    sent_at: Mapped[date | None] = mapped_column(Date)

    # Devise & locale
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    locale: Mapped[str] = mapped_column(String(10), default="fr_FR")
    exchange_rate: Mapped[float] = mapped_column(Numeric(12, 6), default=1)

    # Montants (calculés depuis les lignes)
    subtotal_ht: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    total_discount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    total_tax: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    total_ttc: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    amount_paid: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    amount_due: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    # Détails commerciaux
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)
    payment_method: Mapped[str | None] = mapped_column(String(40))
    # Compte bancaire d'encaissement
    bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_accounts.id", ondelete="SET NULL"))
    notes_public: Mapped[str | None] = mapped_column(Text)  # visible sur la facture
    notes_internal: Mapped[str | None] = mapped_column(Text)
    footer: Mapped[str | None] = mapped_column(Text)

    # Documents générés
    pdf_path: Mapped[str | None] = mapped_column(String(500))

    # Acomptes / récurrence
    is_deposit: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"))
    is_credit_note: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relances
    last_reminder_at: Mapped[date | None] = mapped_column(Date)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)

    # Comptabilisation (écriture générée)
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id", ondelete="SET NULL"))

    items: Mapped[list["InvoiceItem"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceItem.position"
    )
    payments: Mapped[list["Payment"]] = relationship(  # noqa: F821
        back_populates="invoice", cascade="all, delete-orphan"
    )


class InvoiceItem(Base, TenantMixin, TimestampMixin):
    """Ligne de facture."""

    __tablename__ = "invoice_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))

    position: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(14, 4), default=1)
    unit: Mapped[str] = mapped_column(String(20), default="u")
    unit_price: Mapped[float] = mapped_column(Numeric(14, 4), default=0)

    discount_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=0)  # %
    tax_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=0)  # %

    line_subtotal_ht: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    line_tax: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    line_total_ttc: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    invoice: Mapped["Invoice"] = relationship(back_populates="items")
