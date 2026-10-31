from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.deps import get_current_user, get_session
from app.models.client import Client
from app.models.invoice import Invoice, InvoiceItem
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.invoice import InvoiceIn, InvoiceOut, InvoiceUpdate
from app.services.billing import compute_amount_due, compute_document_totals, compute_line
from app.services.email import send_email
from app.services.numbering import next_number
from app.services.pdf import render_pdf, storage_pdf_path
from app.services.plans import enforce_quota

router = APIRouter()


async def _recalculate_invoice(invoice: Invoice) -> None:
    items_data = []
    for it in invoice.items:
        line = compute_line(it.quantity, it.unit_price, it.discount_rate, it.tax_rate)
        it.line_subtotal_ht = line["line_subtotal_ht"]
        it.line_tax = line["line_tax"]
        it.line_total_ttc = line["line_total_ttc"]
        items_data.append(line)
    totals = compute_document_totals(items_data)
    invoice.subtotal_ht = totals["subtotal_ht"]
    invoice.total_tax = totals["total_tax"]
    invoice.total_ttc = totals["total_ttc"]
    invoice.amount_due = compute_amount_due(invoice.total_ttc, invoice.amount_paid or Decimal(0))


@router.get("", response_model=list[InvoiceOut])
async def list_invoices(
    status: str | None = None,
    client_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Invoice)
        .where(Invoice.tenant_id == user.tenant_id)
        .options(selectinload(Invoice.items))
        .order_by(Invoice.issue_date.desc(), Invoice.id.desc())
    )
    if status:
        stmt = stmt.where(Invoice.status == status)
    if client_id:
        stmt = stmt.where(Invoice.client_id == client_id)
    return list(await db.scalars(stmt))


@router.post("", response_model=InvoiceOut)
async def create_invoice(
    payload: InvoiceIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    client = await db.scalar(
        select(Client).where(Client.id == payload.client_id, Client.tenant_id == user.tenant_id)
    )
    if not client:
        raise HTTPException(404, "Client introuvable")

    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    await enforce_quota(db, tenant, "invoice")

    issue_date = payload.issue_date or date.today()
    due_date = payload.due_date or issue_date + timedelta(days=payload.payment_terms_days)

    number = await next_number(db, user.tenant_id, "invoice", issue_date)

    invoice = Invoice(
        tenant_id=user.tenant_id,
        number=number,
        client_id=client.id,
        quote_id=payload.quote_id,
        status="draft",
        issue_date=issue_date,
        due_date=due_date,
        currency=payload.currency,
        locale=payload.locale,
        payment_terms_days=payload.payment_terms_days,
        payment_method=payload.payment_method,
        notes_public=payload.notes_public,
        notes_internal=payload.notes_internal,
        footer=payload.footer,
        is_deposit=payload.is_deposit,
        amount_paid=Decimal(0),
    )
    db.add(invoice)
    await db.flush()

    for i, item_in in enumerate(payload.items):
        line = compute_line(item_in.quantity, item_in.unit_price, item_in.discount_rate, item_in.tax_rate)
        item = InvoiceItem(
            tenant_id=user.tenant_id,
            invoice_id=invoice.id,
            product_id=item_in.product_id,
            position=item_in.position or i,
            description=item_in.description,
            quantity=item_in.quantity,
            unit=item_in.unit,
            unit_price=item_in.unit_price,
            discount_rate=item_in.discount_rate,
            tax_rate=item_in.tax_rate,
            **line,
        )
        db.add(item)
    await db.flush()

    invoice = await db.scalar(
        select(Invoice).where(Invoice.id == invoice.id).options(selectinload(Invoice.items))
    )
    await _recalculate_invoice(invoice)
    await db.commit()
    await db.refresh(invoice)
    return invoice


@router.get("/{invoice_id}", response_model=InvoiceOut)
async def get_invoice(
    invoice_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    invoice = await db.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.tenant_id == user.tenant_id)
        .options(selectinload(Invoice.items))
    )
    if not invoice:
        raise HTTPException(404, "Facture introuvable")
    return invoice


@router.put("/{invoice_id}", response_model=InvoiceOut)
async def update_invoice(
    invoice_id: int,
    payload: InvoiceUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    invoice = await db.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.tenant_id == user.tenant_id)
        .options(selectinload(Invoice.items))
    )
    if not invoice:
        raise HTTPException(404, "Facture introuvable")
    if invoice.status not in ("draft",):
        raise HTTPException(400, "Cette facture n'est plus modifiable")

    data = payload.model_dump(exclude_unset=True)
    if "items" in data:
        items_in = data.pop("items") or []
        for old in list(invoice.items):
            await db.delete(old)
        await db.flush()
        for i, item_data in enumerate(items_in):
            line = compute_line(
                item_data["quantity"], item_data["unit_price"], item_data.get("discount_rate", 0), item_data.get("tax_rate", 0)
            )
            item = InvoiceItem(
                tenant_id=user.tenant_id,
                invoice_id=invoice.id,
                product_id=item_data.get("product_id"),
                position=item_data.get("position", i),
                description=item_data["description"],
                quantity=item_data["quantity"],
                unit=item_data.get("unit", "u"),
                unit_price=item_data["unit_price"],
                discount_rate=item_data.get("discount_rate", 0),
                tax_rate=item_data.get("tax_rate", 0),
                **line,
            )
            db.add(item)
        await db.flush()
        invoice = await db.scalar(
            select(Invoice).where(Invoice.id == invoice_id).options(selectinload(Invoice.items))
        )

    for k, v in data.items():
        setattr(invoice, k, v)

    await _recalculate_invoice(invoice)
    await db.commit()
    await db.refresh(invoice)
    return invoice


@router.post("/{invoice_id}/issue")
async def issue_invoice(
    invoice_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    invoice = await db.scalar(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == user.tenant_id)
    )
    if not invoice:
        raise HTTPException(404, "Facture introuvable")
    if invoice.status == "draft":
        invoice.status = "issued"
    await db.commit()
    return {"status": invoice.status}


@router.post("/{invoice_id}/pdf")
async def generate_pdf(
    invoice_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    invoice = await db.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.tenant_id == user.tenant_id)
        .options(selectinload(Invoice.items))
    )
    if not invoice:
        raise HTTPException(404, "Facture introuvable")
    client = await db.scalar(select(Client).where(Client.id == invoice.client_id))
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))

    out = storage_pdf_path("invoice", user.tenant_id, invoice.number)
    await render_pdf(
        "pdf/invoice.html",
        {"invoice": invoice, "client": client, "tenant": tenant, "settings": settings},
        out,
    )
    invoice.pdf_path = str(out)
    await db.commit()
    return {"pdf_path": invoice.pdf_path}


@router.get("/{invoice_id}/pdf", response_class=FileResponse)
async def download_pdf(
    invoice_id: int,
    inline: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    invoice = await db.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.tenant_id == user.tenant_id)
        .options(selectinload(Invoice.items))
    )
    if not invoice:
        raise HTTPException(404, "Facture introuvable")
    # Toujours régénérer : garantit que le PDF reflète les données et le
    # template actuels (sinon un ancien fichier en cache est servi tel quel).
    client = await db.scalar(select(Client).where(Client.id == invoice.client_id))
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    out = storage_pdf_path("invoice", user.tenant_id, invoice.number)
    await render_pdf(
        "pdf/invoice.html",
        {"invoice": invoice, "client": client, "tenant": tenant, "settings": settings},
        out,
    )
    invoice.pdf_path = str(out)
    await db.commit()
    # inline=1 → aperçu dans le navigateur ; sinon téléchargement.
    disposition = "inline" if inline else "attachment"
    return FileResponse(
        invoice.pdf_path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{invoice.number}.pdf"'},
    )


@router.post("/{invoice_id}/send")
async def send_invoice(
    invoice_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    invoice = await db.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.tenant_id == user.tenant_id)
        .options(selectinload(Invoice.items))
    )
    if not invoice:
        raise HTTPException(404, "Facture introuvable")
    client = await db.scalar(select(Client).where(Client.id == invoice.client_id))
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    if not client.email:
        raise HTTPException(400, "Pas d'email pour ce client")

    out = storage_pdf_path("invoice", user.tenant_id, invoice.number)
    # Régénère systématiquement pour joindre un PDF à jour.
    await render_pdf(
        "pdf/invoice.html",
        {"invoice": invoice, "client": client, "tenant": tenant, "settings": settings},
        out,
    )
    invoice.pdf_path = str(out)

    subject = f"Facture {invoice.number} — {tenant.name}"
    body = (
        f"Bonjour,\n\nVeuillez trouver ci-jointe la facture {invoice.number} "
        f"d'un montant de {invoice.total_ttc} {invoice.currency}.\n\n"
        f"Échéance : {invoice.due_date.isoformat()}.\n\nCordialement,\n{tenant.name}"
    )
    await send_email(to=client.email, subject=subject, body_text=body, attachments=[Path(str(out))])

    invoice.status = "sent" if invoice.status in ("draft", "issued") else invoice.status
    invoice.sent_at = date.today()
    await db.commit()
    return {"sent_to": client.email}


@router.post("/{invoice_id}/cancel")
async def cancel_invoice(
    invoice_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    invoice = await db.scalar(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == user.tenant_id)
    )
    if not invoice:
        raise HTTPException(404, "Facture introuvable")
    invoice.status = "cancelled"
    await db.commit()
    return {"status": "cancelled"}


@router.post("/{invoice_id}/duplicate")
async def duplicate_invoice(
    invoice_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    src = await db.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.tenant_id == user.tenant_id)
        .options(selectinload(Invoice.items))
    )
    if not src:
        raise HTTPException(404, "Facture introuvable")

    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    await enforce_quota(db, tenant, "invoice")

    issue_date = date.today()
    due_date = issue_date + timedelta(days=src.payment_terms_days or 30)
    number = await next_number(db, user.tenant_id, "invoice", issue_date)

    new = Invoice(
        tenant_id=user.tenant_id, number=number, client_id=src.client_id, status="draft",
        issue_date=issue_date, due_date=due_date, currency=src.currency, locale=src.locale,
        payment_terms_days=src.payment_terms_days, payment_method=src.payment_method,
        notes_public=src.notes_public, notes_internal=src.notes_internal, footer=src.footer,
        subtotal_ht=src.subtotal_ht, total_tax=src.total_tax, total_ttc=src.total_ttc,
        total_discount=src.total_discount, amount_paid=Decimal(0), amount_due=src.total_ttc,
    )
    db.add(new)
    await db.flush()
    for it in src.items:
        db.add(
            InvoiceItem(
                tenant_id=user.tenant_id, invoice_id=new.id, product_id=it.product_id,
                position=it.position, description=it.description, quantity=it.quantity,
                unit=it.unit, unit_price=it.unit_price, discount_rate=it.discount_rate,
                tax_rate=it.tax_rate, line_subtotal_ht=it.line_subtotal_ht,
                line_tax=it.line_tax, line_total_ttc=it.line_total_ttc,
            )
        )
    await db.commit()
    return {"invoice_id": new.id, "number": new.number}


@router.post("/{invoice_id}/bank-account")
async def set_invoice_bank_account(
    invoice_id: int,
    bank_account_id: int | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    invoice = await db.scalar(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == user.tenant_id)
    )
    if not invoice:
        raise HTTPException(404, "Facture introuvable")
    if bank_account_id:
        from app.models.bank import BankAccount

        acc = await db.scalar(select(BankAccount).where(
            BankAccount.id == bank_account_id, BankAccount.tenant_id == user.tenant_id))
        invoice.bank_account_id = acc.id if acc else None
    else:
        invoice.bank_account_id = None
    await db.commit()
    return {"bank_account_id": invoice.bank_account_id}


# --- HTML form endpoint ----------------------------------------------------
@router.post("/form")
async def create_invoice_form(
    client_id: int = Form(...),
    issue_date: str | None = Form(None),
    due_date: str | None = Form(None),
    currency: str = Form("EUR"),
    locale: str = Form("fr_FR"),
    payment_terms_days: int = Form(30),
    notes_public: str | None = Form(None),
    bank_account_id: int | None = Form(None),
    descriptions: list[str] = Form([]),
    quantities: list[float] = Form([]),
    unit_prices: list[float] = Form([]),
    tax_rates: list[float] = Form([]),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    items = []
    for i, desc in enumerate(descriptions):
        if not desc:
            continue
        items.append(
            {
                "description": desc,
                "quantity": Decimal(str(quantities[i] if i < len(quantities) else 1)),
                "unit_price": Decimal(str(unit_prices[i] if i < len(unit_prices) else 0)),
                "tax_rate": Decimal(str(tax_rates[i] if i < len(tax_rates) else 0)),
                "discount_rate": Decimal(0),
                "unit": "u",
                "position": i,
            }
        )

    issued = date.fromisoformat(issue_date) if issue_date else date.today()
    payload = InvoiceIn(
        client_id=client_id,
        issue_date=issued,
        due_date=date.fromisoformat(due_date) if due_date else issued + timedelta(days=payment_terms_days),
        currency=currency,
        locale=locale,
        payment_terms_days=payment_terms_days,
        notes_public=notes_public,
        items=items,
    )
    invoice = await create_invoice(payload, user=user, db=db)
    if bank_account_id:
        # vérifie que le compte appartient bien au tenant courant
        from app.models.bank import BankAccount

        acc = await db.scalar(
            select(BankAccount).where(
                BankAccount.id == bank_account_id, BankAccount.tenant_id == user.tenant_id
            )
        )
        if acc:
            invoice.bank_account_id = bank_account_id
            await db.commit()
    return RedirectResponse(url=f"/invoices/{invoice.id}", status_code=303)
