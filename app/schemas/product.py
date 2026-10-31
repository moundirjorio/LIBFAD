from decimal import Decimal

from pydantic import BaseModel, Field


class ProductBase(BaseModel):
    sku: str | None = None
    name: str
    description: str | None = None
    kind: str = "service"
    unit_price: Decimal = Field(default=Decimal(0))
    unit: str = "u"
    tax_rate_id: int | None = None


class ProductIn(ProductBase):
    pass


class ProductOut(ProductBase):
    id: int
    is_active: bool = True

    class Config:
        from_attributes = True


class TaxRateIn(BaseModel):
    name: str
    rate: Decimal
    country: str | None = None
    is_default: bool = False


class TaxRateOut(TaxRateIn):
    id: int

    class Config:
        from_attributes = True
