from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class EmailTemplate(Base, TenantMixin, TimestampMixin):
    """Templates d'emails (invoice_sent, dunning_polite, etc.)."""

    __tablename__ = "email_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    locale: Mapped[str] = mapped_column(String(10), default="fr_FR")
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
