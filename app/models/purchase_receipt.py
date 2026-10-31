from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class PurchaseReceipt(Base, TenantMixin, TimestampMixin):
    """Reçu / facture d'achat pour la pré-comptabilité.

    Chaque pièce est datée, rattachée à un fournisseur (intitulé), affectée à un
    compte comptable, avec son montant TTC et sa TVA. Alimente le journal d'achats.
    """

    __tablename__ = "purchase_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Reçus | Frais à rembourser | Notes de frais
    kind: Mapped[str] = mapped_column(String(20), default="receipt", index=True)

    deposit_date: Mapped[date] = mapped_column(Date, default=date.today, nullable=False)  # date dépôt
    receipt_date: Mapped[date | None] = mapped_column(Date)  # date de la pièce

    label: Mapped[str] = mapped_column(String(200), nullable=False)  # intitulé (ex. GOOGLE)
    supplier_label: Mapped[str | None] = mapped_column(String(200))  # fournisseur normalisé
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"))

    accounting_account: Mapped[str | None] = mapped_column(String(30))  # compte de pièce (ex. 6111)

    amount_ttc: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    amount_tva: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    status: Mapped[str] = mapped_column(String(20), default="to_review", index=True)  # to_review | validated
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)

    # Comptabilisation (écriture générée)
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id", ondelete="SET NULL"))
    # Compte bancaire de paiement
    bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_accounts.id", ondelete="SET NULL"))
