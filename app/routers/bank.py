"""Trésorerie : comptes bancaires et relevés."""

from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_session
from app.models.bank import BankAccount, BankTransaction
from app.models.user import User
from app.services.bank import import_csv, import_pdf, recompute_balance

router = APIRouter()


@router.post("/accounts")
async def create_account(
    name: str = Form(...),
    bank_name: str | None = Form(None),
    iban: str | None = Form(None),
    currency: str = Form("MAD"),
    opening_balance: float = Form(0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    acc = BankAccount(
        tenant_id=user.tenant_id, name=name.strip(), bank_name=(bank_name or None),
        iban=(iban or None), currency=currency,
        opening_balance=Decimal(str(opening_balance or 0)),
        current_balance=Decimal(str(opening_balance or 0)),
    )
    db.add(acc)
    await db.commit()
    return RedirectResponse(url=f"/banque?account={acc.id}", status_code=303)


async def _owned_account(db: AsyncSession, user: User, account_id: int) -> BankAccount:
    acc = await db.scalar(
        select(BankAccount).where(BankAccount.id == account_id, BankAccount.tenant_id == user.tenant_id)
    )
    if not acc:
        raise HTTPException(404, "Compte introuvable")
    return acc


@router.post("/accounts/{account_id}/import")
async def import_statement(
    account_id: int,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    acc = await _owned_account(db, user, account_id)
    data = await file.read()
    name = (file.filename or "").lower()
    is_pdf = data[:5] == b"%PDF-" or name.endswith(".pdf") or (file.content_type == "application/pdf")
    if is_pdf:
        result = await import_pdf(db, user.tenant_id, acc, data)
    else:
        raw = data.decode("utf-8-sig", errors="replace")
        result = await import_csv(db, user.tenant_id, acc, raw)
    return result


@router.post("/transactions/{tx_id}/post")
async def comptabilise_tx(
    tx_id: int,
    counterpart_code: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    from app.services.accounting import post_bank_transaction

    tx = await db.scalar(
        select(BankTransaction).where(
            BankTransaction.id == tx_id, BankTransaction.tenant_id == user.tenant_id
        )
    )
    if not tx:
        raise HTTPException(404, "Mouvement introuvable")
    entry = await post_bank_transaction(db, user.tenant_id, tx, counterpart_code)
    return {"entry_id": entry.id}


@router.post("/transactions/{tx_id}/match")
async def match_transaction(
    tx_id: int,
    invoice_id: int = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Rapproche un encaissement avec une facture : enregistre le paiement et
    met à jour la facture (payée / partiellement payée)."""
    from decimal import Decimal

    from sqlalchemy.orm import selectinload

    from app.models.invoice import Invoice
    from app.models.payment import Payment
    from app.services.billing import compute_amount_due

    tx = await db.scalar(select(BankTransaction).where(
        BankTransaction.id == tx_id, BankTransaction.tenant_id == user.tenant_id))
    if not tx:
        raise HTTPException(404, "Mouvement introuvable")
    invoice = await db.scalar(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == user.tenant_id)
        .options(selectinload(Invoice.payments))
    )
    if not invoice:
        raise HTTPException(404, "Facture introuvable")

    db.add(Payment(
        tenant_id=user.tenant_id, invoice_id=invoice.id, paid_on=tx.op_date,
        amount=Decimal(str(tx.amount)), currency=invoice.currency, method="transfer",
        reference=(tx.label or "")[:200], notes="Rapprochement bancaire",
    ))
    await db.flush()
    total_paid = sum((Decimal(str(p.amount)) for p in invoice.payments), Decimal(0)) + Decimal(str(tx.amount))
    invoice.amount_paid = total_paid
    invoice.amount_due = compute_amount_due(invoice.total_ttc, total_paid)
    if invoice.amount_due <= 0:
        invoice.status = "paid"
        invoice.paid_at = tx.op_date
    elif total_paid > 0:
        invoice.status = "partially_paid"
    tx.reconciled = True
    await db.commit()
    return {"matched": True, "invoice": invoice.number, "status": invoice.status}


@router.post("/transactions/{tx_id}/reconcile")
async def reconcile_tx(
    tx_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    tx = await db.scalar(
        select(BankTransaction).where(
            BankTransaction.id == tx_id, BankTransaction.tenant_id == user.tenant_id
        )
    )
    if not tx:
        raise HTTPException(404, "Mouvement introuvable")
    tx.reconciled = not tx.reconciled
    await db.commit()
    return {"reconciled": tx.reconciled}
