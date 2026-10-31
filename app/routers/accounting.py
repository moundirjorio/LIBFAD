"""Pré-comptabilité : gestion des reçus / factures d'achat."""

from datetime import date
from decimal import Decimal

from datetime import date as _date

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_session
from app.models.invoice import Invoice
from app.models.purchase_receipt import PurchaseReceipt
from app.models.user import User
from app.services.accounting import (
    FEC_HEADER,
    create_entry,
    fec_rows,
    post_invoice_entry,
    post_purchase_entry,
    post_vat_entry,
    seed_ledger,
)

router = APIRouter()

_KINDS = {"receipt", "expense", "expense_report"}


# --- Moteur comptable (partie double) ---------------------------------------
class EntryLineIn(BaseModel):
    account_id: int
    label: str | None = None
    debit: float = 0
    credit: float = 0


class EntryIn(BaseModel):
    journal_id: int
    entry_date: date | None = None
    reference: str | None = None
    label: str | None = None
    lines: list[EntryLineIn] = []


@router.post("/ledger/seed")
async def seed_chart(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return await seed_ledger(db, user.tenant_id)


@router.post("/entries")
async def post_entry(
    payload: EntryIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    entry = await create_entry(
        db, user.tenant_id, payload.journal_id,
        payload.entry_date or date.today(), payload.reference, payload.label,
        [ln.model_dump() for ln in payload.lines],
    )
    return {"entry_id": entry.id, "total_debit": float(entry.total_debit)}


@router.get("/export/fec", response_class=PlainTextResponse)
async def export_fec(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Export FEC (Fichier des Écritures Comptables) — à transmettre à l'expert-comptable."""
    rows = await fec_rows(db, user.tenant_id)
    content = "\r\n".join(["\t".join(FEC_HEADER)] + ["\t".join(r) for r in rows])
    filename = f"FEC_{user.tenant_id}_{_date.today():%Y%m%d}.txt"
    return PlainTextResponse(
        content, headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.post("/vat/post")
async def comptabilise_vat(
    year: int = Form(...),
    month: int | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    entry = await post_vat_entry(db, user.tenant_id, year, month or None)
    return {"entry_id": entry.id}


@router.post("/mileage")
async def create_mileage(
    trip_date: str | None = Form(None),
    motif: str = Form(...),
    distance_km: float = Form(...),
    rate: float = Form(2.5),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Indemnité kilométrique : distance × barème → note de frais."""
    km = Decimal(str(distance_km or 0))
    r = Decimal(str(rate or 0))
    amount = (km * r).quantize(Decimal("0.01"))
    rec = PurchaseReceipt(
        tenant_id=user.tenant_id,
        kind="expense_report",
        deposit_date=date.today(),
        receipt_date=date.fromisoformat(trip_date) if trip_date else date.today(),
        label=f"IK — {motif.strip()}",
        accounting_account="61425",  # Transports et déplacements
        amount_ttc=amount,
        amount_tva=Decimal(0),
        note=f"{km} km × {r} MAD/km",
    )
    db.add(rec)
    await db.commit()
    return RedirectResponse(url="/achats?kind=expense_report", status_code=303)


@router.post("/invoices/{invoice_id}/post")
async def comptabilise_invoice(
    invoice_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    inv = await db.scalar(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == user.tenant_id)
    )
    if not inv:
        raise HTTPException(404, "Facture introuvable")
    entry = await post_invoice_entry(db, user.tenant_id, inv)
    return {"entry_id": entry.id}


@router.post("/purchases/{receipt_id}/post")
async def comptabilise_purchase(
    receipt_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    rec = await db.scalar(
        select(PurchaseReceipt).where(
            PurchaseReceipt.id == receipt_id, PurchaseReceipt.tenant_id == user.tenant_id
        )
    )
    if not rec:
        raise HTTPException(404, "Pièce introuvable")
    entry = await post_purchase_entry(db, user.tenant_id, rec)
    return {"entry_id": entry.id}


@router.post("/form")
async def create_receipt(
    kind: str = Form("receipt"),
    deposit_date: str | None = Form(None),
    receipt_date: str | None = Form(None),
    label: str = Form(...),
    accounting_account: str | None = Form(None),
    amount_ttc: float = Form(0),
    amount_tva: float = Form(0),
    bank_account_id: int | None = Form(None),
    supplier_id: int | None = Form(None),
    note: str | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    if kind not in _KINDS:
        kind = "receipt"
    rec = PurchaseReceipt(
        tenant_id=user.tenant_id,
        kind=kind,
        deposit_date=date.fromisoformat(deposit_date) if deposit_date else date.today(),
        receipt_date=date.fromisoformat(receipt_date) if receipt_date else None,
        label=label.strip(),
        supplier_label=(label.strip() or None),
        accounting_account=(accounting_account or "").strip() or None,
        amount_ttc=Decimal(str(amount_ttc or 0)),
        amount_tva=Decimal(str(amount_tva or 0)),
        bank_account_id=bank_account_id or None,
        supplier_id=supplier_id or None,
        note=note,
    )
    db.add(rec)
    await db.commit()
    return RedirectResponse(url=f"/achats?kind={kind}", status_code=303)


async def _get_owned(db: AsyncSession, user: User, receipt_id: int) -> PurchaseReceipt:
    rec = await db.scalar(
        select(PurchaseReceipt).where(
            PurchaseReceipt.id == receipt_id, PurchaseReceipt.tenant_id == user.tenant_id
        )
    )
    if not rec:
        raise HTTPException(404, "Pièce introuvable")
    return rec


@router.post("/{receipt_id}/validate")
async def validate_receipt(
    receipt_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    rec = await _get_owned(db, user, receipt_id)
    rec.status = "validated" if rec.status != "validated" else "to_review"
    await db.commit()
    return {"status": rec.status}


@router.post("/{receipt_id}/delete")
async def delete_receipt(
    receipt_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    rec = await _get_owned(db, user, receipt_id)
    await db.delete(rec)
    await db.commit()
    return {"deleted": True}
