from pydantic import BaseModel, Field

from app.schemas.types import Email


class RegisterIn(BaseModel):
    company_name: str = Field(min_length=2, max_length=200)
    email: Email
    password: str = Field(min_length=6, max_length=200)
    full_name: str = Field(min_length=2, max_length=200)
    country: str = Field(default="FR", min_length=2, max_length=2)
    currency: str = Field(default="EUR")
    locale: str = Field(default="fr_FR")


class LoginIn(BaseModel):
    email: Email
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: Email
    full_name: str
    role: str
    tenant_id: int

    class Config:
        from_attributes = True
