"""Fournisseurs (achats)."""

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_session
from app.models.supplier import Supplier
from app.models.user import User

router = APIRouter()


@router.post("/form")
async def create_supplier_form(
    name: str = Form(...),
    email: str | None = Form(None),
    phone: str | None = Form(None),
    ice: str | None = Form(None),
    vat_number: str | None = Form(None),
    city: str | None = Form(None),
    country: str = Form("MA"),
    default_account: str | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    supplier = Supplier(
        tenant_id=user.tenant_id, name=name.strip(), email=(email or None),
        phone=(phone or None), ice=(ice or None), vat_number=(vat_number or None),
        city=(city or None), country=country,
        default_account=(default_account or None),
    )
    db.add(supplier)
    await db.commit()
    return RedirectResponse(url="/fournisseurs", status_code=303)


@router.delete("/{supplier_id}")
@router.post("/{supplier_id}/delete")
async def delete_supplier(
    supplier_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    supplier = await db.scalar(
        select(Supplier).where(Supplier.id == supplier_id, Supplier.tenant_id == user.tenant_id)
    )
    if not supplier:
        raise HTTPException(404, "Fournisseur introuvable")
    await db.delete(supplier)
    await db.commit()
    return {"deleted": True}
