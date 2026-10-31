from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class InvoiceItemIn(BaseModel):
    description: str
    quantity: Decimal = Field(default=Decimal(1))
    unit: str = "u"
    unit_price: Decimal = Field(default=Decimal(0))
    discount_rate: Decimal = Field(default=Decimal(0))
    tax_rate: Decimal = Field(default=Decimal(0))
    product_id: int | None = None
    position: int = 0


class InvoiceItemOut(InvoiceItemIn):
    id: int
    line_subtotal_ht: Decimal
    line_tax: Decimal
    line_total_ttc: Decimal

    class Config:
        from_attributes = True


class InvoiceBase(BaseModel):
    client_id: int
    issue_date: date | None = None
    due_date: date | None = None
    currency: str = "EUR"
    locale: str = "fr_FR"
    payment_terms_days: int = 30
    payment_method: str | None = None
    notes_public: str | None = None
    notes_internal: str | None = None
    footer: str | None = None
    is_deposit: bool = False
    quote_id: int | None = None


class InvoiceIn(InvoiceBase):
    items: list[InvoiceItemIn] = []


class InvoiceUpdate(BaseModel):
    issue_date: date | None = None
    due_date: date | None = None
    payment_terms_days: int | None = None
    payment_method: str | None = None
    notes_public: str | None = None
    notes_internal: str | None = None
    footer: str | None = None
    status: str | None = None
    items: list[InvoiceItemIn] | None = None


class InvoiceOut(InvoiceBase):
    id: int
    number: str
    status: str
    issue_date: date
    due_date: date
    paid_at: date | None
    subtotal_ht: Decimal
    total_tax: Decimal
    total_ttc: Decimal
    amount_paid: Decimal
    amount_due: Decimal
    pdf_path: str | None
    reminder_count: int
    items: list[InvoiceItemOut] = []

    class Config:
        from_attributes = True
