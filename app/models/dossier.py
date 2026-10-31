from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class DossierAccess(Base, TimestampMixin):
    """Accès d'un utilisateur (cabinet) à un dossier (société / tenant).

    Permet à un compte de gérer plusieurs sociétés clientes et de basculer
    de l'une à l'autre. Table transverse : pas de TenantMixin.
    """

    __tablename__ = "dossier_access"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id", name="uq_dossier_user_tenant"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(30), default="collaborator")  # owner|collaborator
