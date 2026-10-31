from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Payment(Base, TenantMixin, TimestampMixin):
    """Encaissement de paiement (peut être partiel)."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)

    paid_on: Mapped[date] = mapped_column(Date, default=date.today, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    method: Mapped[str] = mapped_column(String(40), default="transfer")
    # transfer | card | cash | check | direct_debit | other

    reference: Mapped[str | None] = mapped_column(String(200))  # n° transaction, chèque, etc.
    notes: Mapped[str | None] = mapped_column(Text)

    invoice: Mapped["Invoice"] = relationship(back_populates="payments")  # noqa: F821
