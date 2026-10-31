from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class BankAccount(Base, TenantMixin, TimestampMixin):
    """Compte bancaire de l'entreprise (trésorerie)."""

    __tablename__ = "bank_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)  # ex. Compte courant
    bank_name: Mapped[str | None] = mapped_column(String(120))
    iban: Mapped[str | None] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(3), default="MAD")

    opening_balance: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    current_balance: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    transactions: Mapped[list["BankTransaction"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class BankTransaction(Base, TenantMixin, TimestampMixin):
    """Mouvement bancaire. `amount` signé : + encaissement, − décaissement."""

    __tablename__ = "bank_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id", ondelete="CASCADE"), index=True)

    op_date: Mapped[date] = mapped_column(Date, default=date.today, nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)  # signé

    category: Mapped[str | None] = mapped_column(String(60))
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[str | None] = mapped_column(Text)
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id", ondelete="SET NULL"))

    account: Mapped["BankAccount"] = relationship(back_populates="transactions")
