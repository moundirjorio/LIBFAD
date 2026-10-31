from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Contract(Base, TenantMixin, TimestampMixin):
    """Contrat (génération IA possible)."""

    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(80), index=True)
    kind: Mapped[str] = mapped_column(String(40), default="service")
    # service | maintenance | sale | nda | other

    status: Mapped[str] = mapped_column(String(20), default="draft")
    # draft | sent | signed | expired | terminated

    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[float | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str | None] = mapped_column(String(3))

    body: Mapped[str | None] = mapped_column(Text)  # markdown
    pdf_path: Mapped[str | None] = mapped_column(String(500))
    ai_generated: Mapped[bool] = mapped_column(default=False)
