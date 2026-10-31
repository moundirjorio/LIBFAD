from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class PaymentIn(BaseModel):
    invoice_id: int
    paid_on: date | None = None
    amount: Decimal
    currency: str = "EUR"
    method: str = "transfer"
    reference: str | None = None
    notes: str | None = None


class PaymentOut(PaymentIn):
    id: int

    class Config:
        from_attributes = True
