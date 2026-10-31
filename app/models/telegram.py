from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class TelegramLink(Base, TenantMixin, TimestampMixin):
    """Lien entre une conversation Telegram et un utilisateur LIBFAD.

    Une ligne naît « en attente » (code de liaison à usage unique, sans chat_id)
    depuis l'interface, puis est activée quand l'utilisateur envoie
    `/start <code>` au bot de la société. Sans lien actif, le bot ne répond
    à aucune demande métier.
    """

    __tablename__ = "telegram_links"
    __table_args__ = (UniqueConstraint("tenant_id", "chat_id", name="uq_telegram_links_tenant_chat"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    chat_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    tg_username: Mapped[str | None] = mapped_column(String(64))
    tg_name: Mapped[str | None] = mapped_column(String(200))

    # Code de liaison à usage unique (effacé une fois la conversation liée)
    code: Mapped[str | None] = mapped_column(String(64), index=True)
    code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
