from datetime import date

from sqlalchemy import Date, ForeignKey, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Document(Base, TenantMixin, TimestampMixin):
    """Document scanné / fichier client (facture fournisseur, contrat...)."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))

    # Métadonnées
    kind: Mapped[str] = mapped_column(String(40), default="supplier_invoice", index=True)
    # supplier_invoice | contract | receipt | bank_statement | other

    original_filename: Mapped[str] = mapped_column(String(300))
    file_path: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str | None] = mapped_column(String(100))
    file_size: Mapped[int | None] = mapped_column()

    # Statut OCR
    ocr_status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending | processing | done | failed
    ocr_engine: Mapped[str | None] = mapped_column(String(40), default="claude_vision")
    ocr_raw_text: Mapped[str | None] = mapped_column(Text)
    ocr_error: Mapped[str | None] = mapped_column(Text)

    # Données extraites (cache structuré)
    supplier_name: Mapped[str | None] = mapped_column(String(200), index=True)
    supplier_vat: Mapped[str | None] = mapped_column(String(50))
    document_number: Mapped[str | None] = mapped_column(String(100))
    document_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)

    currency: Mapped[str | None] = mapped_column(String(3))
    total_ht: Mapped[float | None] = mapped_column(Numeric(14, 2))
    total_tax: Mapped[float | None] = mapped_column(Numeric(14, 2))
    total_ttc: Mapped[float | None] = mapped_column(Numeric(14, 2))

    # Données détaillées (JSON brut)
    extracted_data: Mapped[dict | None] = mapped_column(JSON)

    # Statut métier
    status: Mapped[str] = mapped_column(String(20), default="new")
    # new | reviewed | approved | rejected | paid

    notes: Mapped[str | None] = mapped_column(Text)
