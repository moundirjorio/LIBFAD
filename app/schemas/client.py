from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.types import Email


class ContactIn(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    email: Email | None = None
    phone: str | None = None
    mobile: str | None = None
    is_primary: bool = False
    receives_invoices: bool = True
    receives_dunning: bool = True
    notes: str | None = None


class ContactOut(ContactIn):
    id: int

    class Config:
        from_attributes = True


class ClientBase(BaseModel):
    client_type: str = "company"
    name: str
    legal_name: str | None = None
    code: str | None = None
    siret: str | None = None
    vat_number: str | None = None
    ice: str | None = None
    email: Email | None = None
    phone: str | None = None
    website: str | None = None

    billing_address_line1: str | None = None
    billing_address_line2: str | None = None
    billing_postal_code: str | None = None
    billing_city: str | None = None
    billing_country: str | None = "FR"

    shipping_address_line1: str | None = None
    shipping_address_line2: str | None = None
    shipping_postal_code: str | None = None
    shipping_city: str | None = None
    shipping_country: str | None = None

    currency: str = "EUR"
    locale: str = "fr_FR"
    payment_terms_days: int = 30
    payment_method: str | None = None
    discount_rate: Decimal = Field(default=Decimal(0))
    credit_limit: Decimal | None = None
    notes: str | None = None
    tags: str | None = None


class ClientIn(ClientBase):
    pass


class ClientUpdate(ClientBase):
    name: str | None = None  # type: ignore[assignment]


class ClientOut(ClientBase):
    id: int
    solvency_score: int | None = None
    risk_level: str = "unknown"
    is_active: bool = True

    class Config:
        from_attributes = True
