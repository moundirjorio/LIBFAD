from sqlalchemy import Boolean, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class TaxRate(Base, TenantMixin, TimestampMixin):
    """Taux de TVA configurable (multi-TVA)."""

    __tablename__ = "tax_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)  # ex: "TVA 20%"
    rate: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)  # pourcentage 0..100
    country: Mapped[str | None] = mapped_column(String(2))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
