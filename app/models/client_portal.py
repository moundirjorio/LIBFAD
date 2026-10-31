from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class ClientPortalUser(Base, TenantMixin, TimestampMixin):
    """Compte de connexion d'un client à son espace (portail client).

    Séparé de `User` (utilisateurs de l'entreprise) : un client se connecte
    uniquement au portail `/portail`, jamais à l'interface de gestion.
    """

    __tablename__ = "client_portal_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )

    email: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    client: Mapped["Client"] = relationship()  # noqa: F821
