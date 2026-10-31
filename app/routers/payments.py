from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.deps import get_current_user, get_session
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.user import User
from app.schemas.payment import PaymentIn, PaymentOut
from app.services.billing import compute_amount_due

router = APIRouter()


@router.get("", response_model=list[PaymentOut])
async def list_payments(
    invoice_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    stmt = select(Payment).where(Payment.tenant_id == user.tenant_id).order_by(Payment.paid_on.desc())
    if invoice_id:
        stmt = stmt.where(Payment.invoice_id == invoice_id)
    return list(await db.scalars(stmt))


@router.post("", response_model=PaymentOut)
async def record_payment(
    payload: PaymentIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    invoice = await db.scalar(
        select(Invoice)
        .where(Invoice.id == payload.invoice_id, Invoice.tenant_id == user.tenant_id)
        .options(selectinload(Invoice.payments))
    )
    if not invoice:
        raise HTTPException(404, "Facture introuvable")

    payment = Payment(
        tenant_id=user.tenant_id,
        invoice_id=invoice.id,
        paid_on=payload.paid_on or date.today(),
        amount=payload.amount,
        currency=payload.currency or invoice.currency,
        method=payload.method,
        reference=payload.reference,
        notes=payload.notes,
    )
    db.add(payment)
    await db.flush()

    # Recalcul des montants de la facture
    total_paid = Decimal(0)
    for p in invoice.payments:
        total_paid += Decimal(str(p.amount))
    total_paid += Decimal(str(payload.amount))
    invoice.amount_paid = total_paid
    invoice.amount_due = compute_amount_due(invoice.total_ttc, total_paid)
    if invoice.amount_due <= 0:
        invoice.status = "paid"
        invoice.paid_at = payment.paid_on
    elif total_paid > 0:
        invoice.status = "partially_paid"
    await db.commit()
    await db.refresh(payment)
    return payment


@router.delete("/{payment_id}")
async def delete_payment(
    payment_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    payment = await db.scalar(
        select(Payment).where(Payment.id == payment_id, Payment.tenant_id == user.tenant_id)
    )
    if not payment:
        raise HTTPException(404, "Paiement introuvable")
    invoice = await db.scalar(
        select(Invoice).where(Invoice.id == payment.invoice_id).options(selectinload(Invoice.payments))
    )
    await db.delete(payment)
    await db.flush()

    if invoice:
        total_paid = Decimal(0)
        for p in invoice.payments:
            if p.id != payment_id:
                total_paid += Decimal(str(p.amount))
        invoice.amount_paid = total_paid
        invoice.amount_due = compute_amount_due(invoice.total_ttc, total_paid)
        if invoice.amount_due <= 0:
            invoice.status = "paid"
        elif total_paid > 0:
            invoice.status = "partially_paid"
        else:
            invoice.status = "issued"
            invoice.paid_at = None
    await db.commit()
    return {"deleted": True}
