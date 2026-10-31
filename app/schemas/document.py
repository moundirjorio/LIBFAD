from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: int
    kind: str
    original_filename: str
    ocr_status: str
    supplier_name: str | None
    document_number: str | None
    document_date: date | None
    due_date: date | None
    currency: str | None
    total_ht: Decimal | None
    total_tax: Decimal | None
    total_ttc: Decimal | None
    status: str

    class Config:
        from_attributes = True


class ExtractedInvoiceData(BaseModel):
    supplier_name: str | None = None
    supplier_vat: str | None = None
    document_number: str | None = None
    document_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    total_ht: Decimal | None = None
    total_tax: Decimal | None = None
    total_ttc: Decimal | None = None
    line_items: list[dict] = []
