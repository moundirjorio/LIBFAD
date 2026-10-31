from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class SubscriptionIn(BaseModel):
    client_id: int
    product_id: int | None = None
    name: str
    description: str | None = None
    period: str = "monthly"
    interval: int = 1
    amount_ht: Decimal
    tax_rate: Decimal = Decimal(0)
    currency: str = "EUR"
    start_date: date | None = None
    end_date: date | None = None
    next_billing_date: date | None = None
    auto_send: bool = True


class SubscriptionOut(SubscriptionIn):
    id: int
    is_active: bool = True
    last_billing_date: date | None = None

    class Config:
        from_attributes = True
