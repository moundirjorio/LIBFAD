from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Notification(Base, TenantMixin, TimestampMixin):
    """Notification interne (pour un user du tenant)."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    kind: Mapped[str] = mapped_column(String(40), default="info")
    # info | warning | success | error | dunning | payment | document
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(300))

    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Task(Base, TenantMixin, TimestampMixin):
    """Tâche interne (manuelle ou générée par l'IA)."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"))

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    priority: Mapped[str] = mapped_column(String(10), default="normal")  # low|normal|high|urgent
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|in_progress|done|cancelled
    due_date: Mapped[date | None] = mapped_column(Date)

    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
