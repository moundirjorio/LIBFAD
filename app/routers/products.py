from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_session
from app.models.product import Product
from app.models.tax_rate import TaxRate
from app.models.user import User
from app.schemas.product import ProductIn, ProductOut, TaxRateIn, TaxRateOut

router = APIRouter()


def _to_decimal(value: str | None, default: Decimal = Decimal(0)) -> Decimal:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default


@router.get("", response_model=list[ProductOut])
async def list_products(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    return list(await db.scalars(select(Product).where(Product.tenant_id == user.tenant_id)))


@router.post("", response_model=ProductOut)
async def create_product(
    payload: ProductIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    product = Product(tenant_id=user.tenant_id, **payload.model_dump())
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product


@router.delete("/{product_id}")
async def delete_product(
    product_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    product = await db.scalar(
        select(Product).where(Product.id == product_id, Product.tenant_id == user.tenant_id)
    )
    if not product:
        raise HTTPException(404, "Produit introuvable")
    await db.delete(product)
    await db.commit()
    return {"deleted": True}


# --- Tax rates --------------------------------------------------------------
@router.get("/tax/rates", response_model=list[TaxRateOut])
async def list_tax_rates(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    return list(await db.scalars(select(TaxRate).where(TaxRate.tenant_id == user.tenant_id)))


@router.post("/tax/rates", response_model=TaxRateOut)
async def create_tax_rate(
    payload: TaxRateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    tr = TaxRate(tenant_id=user.tenant_id, **payload.model_dump())
    db.add(tr)
    await db.commit()
    await db.refresh(tr)
    return tr


# --- HTML form endpoints ----------------------------------------------------
@router.post("/form")
async def create_product_form(
    name: str = Form(...),
    sku: str | None = Form(None),
    description: str | None = Form(None),
    kind: str = Form("service"),
    unit_price: str = Form("0"),
    unit: str = Form("u"),
    tax_rate_id: int | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    product = Product(
        tenant_id=user.tenant_id,
        name=name,
        sku=sku or None,
        description=description or None,
        kind=kind,
        unit_price=_to_decimal(unit_price),
        unit=unit,
        tax_rate_id=tax_rate_id,
    )
    db.add(product)
    await db.commit()
    return RedirectResponse(url="/products", status_code=303)


@router.post("/{product_id}/delete-form")
async def delete_product_form(
    product_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    product = await db.scalar(
        select(Product).where(Product.id == product_id, Product.tenant_id == user.tenant_id)
    )
    if product:
        await db.delete(product)
        await db.commit()
    return RedirectResponse(url="/products", status_code=303)
