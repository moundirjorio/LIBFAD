from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class PasswordResetToken(Base, TimestampMixin):
    """Jeton de réinitialisation de mot de passe (haché, usage unique, expirable).

    `scope` distingue les comptes entreprise ("company", table users) des comptes
    du portail client ("portal", table client_portal_users). On ne stocke que le
    hash du jeton ; le jeton en clair n'existe que dans le lien envoyé par email.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)  # company | portal
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
