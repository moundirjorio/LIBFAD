"""Moteur comptable en partie double (plan comptable CGNC, journaux, écritures).

- LedgerAccount : un compte du plan comptable (ex. 3421 Clients).
- Journal       : un journal (Achats, Ventes, Banque, Caisse, OD).
- JournalEntry  : une écriture (pièce) datée, dans un journal.
- JournalEntryLine : une ligne d'écriture (compte + débit/crédit).

Règle d'or : pour chaque écriture, Σ débits = Σ crédits.
"""

from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class LedgerAccount(Base, TenantMixin, TimestampMixin):
    """Compte du plan comptable (CGNC)."""

    __tablename__ = "ledger_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # ex. 3421
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    account_class: Mapped[int] = mapped_column(Integer, default=0)  # 1..8 (classe CGNC)
    # actif | passif | charge | produit | tresorerie
    nature: Mapped[str] = mapped_column(String(20), default="")


class Journal(Base, TenantMixin, TimestampMixin):
    """Journal comptable."""

    __tablename__ = "journals"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # AC/VE/BQ/CA/OD
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[str] = mapped_column(String(20), default="od")  # achats|ventes|banque|caisse|od


class JournalEntry(Base, TenantMixin, TimestampMixin):
    """Écriture comptable (pièce) : un ensemble de lignes équilibrées."""

    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_id: Mapped[int] = mapped_column(ForeignKey("journals.id", ondelete="RESTRICT"), index=True)
    entry_date: Mapped[date] = mapped_column(Date, default=date.today, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(60))  # n° pièce
    label: Mapped[str | None] = mapped_column(String(200))  # libellé de l'écriture

    total_debit: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    total_credit: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    lines: Mapped[list["JournalEntryLine"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="JournalEntryLine.position"
    )


class JournalEntryLine(Base, TenantMixin, TimestampMixin):
    """Ligne d'écriture : un compte mouvementé au débit ou au crédit."""

    __tablename__ = "journal_entry_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), index=True)

    position: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str | None] = mapped_column(String(200))
    debit: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    credit: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    entry: Mapped["JournalEntry"] = relationship(back_populates="lines")
    account: Mapped["LedgerAccount"] = relationship()
