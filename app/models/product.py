from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Product(Base, TenantMixin, TimestampMixin):
    """Produit ou service du catalogue."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)

    sku: Mapped[str | None] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20), default="service")  # service|product

    unit_price: Mapped[float] = mapped_column(Numeric(14, 4), default=0)  # HT
    unit: Mapped[str] = mapped_column(String(20), default="u")  # u, h, j, mois...

    tax_rate_id: Mapped[int | None] = mapped_column(ForeignKey("tax_rates.id"))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
