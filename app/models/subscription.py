from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Subscription(Base, TenantMixin, TimestampMixin):
    """Abonnement / facture récurrente."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))

    period: Mapped[str] = mapped_column(String(20), default="monthly")
    # daily | weekly | monthly | quarterly | yearly
    interval: Mapped[int] = mapped_column(Integer, default=1)  # tous les N périodes

    amount_ht: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    tax_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")

    start_date: Mapped[date] = mapped_column(Date, default=date.today)
    end_date: Mapped[date | None] = mapped_column(Date)
    next_billing_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_billing_date: Mapped[date | None] = mapped_column(Date)

    auto_send: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
