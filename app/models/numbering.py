from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class NumberingSequence(Base, TenantMixin, TimestampMixin):
    """Compteurs de numérotation (factures, devis, avoirs) par tenant et par année."""

    __tablename__ = "numbering_sequences"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "year", name="uq_numbering_tenant_kind_year"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # invoice|quote|credit_note
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    prefix: Mapped[str] = mapped_column(String(20), default="")  # ex: "FA-"
    last_value: Mapped[int] = mapped_column(Integer, default=0)
    padding: Mapped[int] = mapped_column(Integer, default=5)  # ex: 00001
