from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class QuoteItemIn(BaseModel):
    description: str
    quantity: Decimal = Field(default=Decimal(1))
    unit: str = "u"
    unit_price: Decimal = Field(default=Decimal(0))
    discount_rate: Decimal = Field(default=Decimal(0))
    tax_rate: Decimal = Field(default=Decimal(0))
    product_id: int | None = None
    position: int = 0


class QuoteItemOut(QuoteItemIn):
    id: int
    line_subtotal_ht: Decimal
    line_tax: Decimal
    line_total_ttc: Decimal

    class Config:
        from_attributes = True


class QuoteIn(BaseModel):
    client_id: int
    issue_date: date | None = None
    valid_until: date | None = None
    currency: str = "EUR"
    locale: str = "fr_FR"
    notes_public: str | None = None
    notes_internal: str | None = None
    footer: str | None = None
    items: list[QuoteItemIn] = []


class QuoteOut(QuoteIn):
    id: int
    number: str
    status: str
    issue_date: date
    valid_until: date
    subtotal_ht: Decimal
    total_tax: Decimal
    total_ttc: Decimal
    pdf_path: str | None
    converted_invoice_id: int | None
    items: list[QuoteItemOut] = []

    class Config:
        from_attributes = True
