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
from app.models.quote import Quote, QuoteItem
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.quote import QuoteIn, QuoteOut
from app.services.billing import compute_document_totals, compute_line
from app.services.email import send_email
from app.services.numbering import next_number
from app.services.pdf import render_pdf, storage_pdf_path

router = APIRouter()


@router.get("", response_model=list[QuoteOut])
async def list_quotes(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return list(
        await db.scalars(
            select(Quote)
            .where(Quote.tenant_id == user.tenant_id)
            .options(selectinload(Quote.items))
            .order_by(Quote.issue_date.desc(), Quote.id.desc())
        )
    )


@router.post("", response_model=QuoteOut)
async def create_quote(
    payload: QuoteIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    issue_date = payload.issue_date or date.today()
    valid_until = payload.valid_until or issue_date + timedelta(days=30)
    number = await next_number(db, user.tenant_id, "quote", issue_date)

    quote = Quote(
        tenant_id=user.tenant_id,
        number=number,
        client_id=payload.client_id,
        status="draft",
        issue_date=issue_date,
        valid_until=valid_until,
        currency=payload.currency,
        locale=payload.locale,
        notes_public=payload.notes_public,
        notes_internal=payload.notes_internal,
        footer=payload.footer,
    )
    db.add(quote)
    await db.flush()

    items_data = []
    for i, item_in in enumerate(payload.items):
        line = compute_line(item_in.quantity, item_in.unit_price, item_in.discount_rate, item_in.tax_rate)
        items_data.append(line)
        db.add(
            QuoteItem(
                tenant_id=user.tenant_id,
                quote_id=quote.id,
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
        )
    totals = compute_document_totals(items_data)
    quote.subtotal_ht = totals["subtotal_ht"]
    quote.total_tax = totals["total_tax"]
    quote.total_ttc = totals["total_ttc"]
    await db.commit()
    quote = await db.scalar(
        select(Quote).where(Quote.id == quote.id).options(selectinload(Quote.items))
    )
    return quote


@router.post("/{quote_id}/duplicate")
async def duplicate_quote(
    quote_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    src = await db.scalar(
        select(Quote)
        .where(Quote.id == quote_id, Quote.tenant_id == user.tenant_id)
        .options(selectinload(Quote.items))
    )
    if not src:
        raise HTTPException(404, "Devis introuvable")

    issue_date = date.today()
    valid_until = issue_date + timedelta(days=30)
    number = await next_number(db, user.tenant_id, "quote", issue_date)

    new = Quote(
        tenant_id=user.tenant_id, number=number, client_id=src.client_id, status="draft",
        issue_date=issue_date, valid_until=valid_until, currency=src.currency, locale=src.locale,
        subtotal_ht=src.subtotal_ht, total_tax=src.total_tax, total_ttc=src.total_ttc,
        total_discount=src.total_discount, notes_public=src.notes_public,
        notes_internal=src.notes_internal, footer=src.footer,
    )
    db.add(new)
    await db.flush()
    for it in src.items:
        db.add(
            QuoteItem(
                tenant_id=user.tenant_id, quote_id=new.id, product_id=it.product_id,
                position=it.position, description=it.description, quantity=it.quantity,
                unit=it.unit, unit_price=it.unit_price, discount_rate=it.discount_rate,
                tax_rate=it.tax_rate, line_subtotal_ht=it.line_subtotal_ht,
                line_tax=it.line_tax, line_total_ttc=it.line_total_ttc,
            )
        )
    await db.commit()
    return {"quote_id": new.id, "number": new.number}


# --- HTML form endpoint -----------------------------------------------------
@router.post("/form")
async def create_quote_form(
    client_id: int = Form(...),
    issue_date: str | None = Form(None),
    valid_until: str | None = Form(None),
    validity_days: int = Form(30),
    currency: str = Form("EUR"),
    locale: str = Form("fr_FR"),
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
    payload = QuoteIn(
        client_id=client_id,
        issue_date=issued,
        valid_until=date.fromisoformat(valid_until) if valid_until else issued + timedelta(days=validity_days),
        currency=currency,
        locale=locale,
        notes_public=notes_public,
        items=items,
    )
    quote = await create_quote(payload, user=user, db=db)
    if bank_account_id:
        from app.models.bank import BankAccount

        acc = await db.scalar(select(BankAccount).where(
            BankAccount.id == bank_account_id, BankAccount.tenant_id == user.tenant_id))
        if acc:
            quote.bank_account_id = bank_account_id
            await db.commit()
    return RedirectResponse(url=f"/quotes/{quote.id}", status_code=303)


@router.post("/{quote_id}/accept")
async def accept_quote(
    quote_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    quote = await db.scalar(
        select(Quote).where(Quote.id == quote_id, Quote.tenant_id == user.tenant_id)
    )
    if not quote:
        raise HTTPException(404, "Devis introuvable")
    quote.status = "accepted"
    quote.accepted_at = date.today()
    await db.commit()
    return {"status": "accepted"}


@router.post("/{quote_id}/convert", response_model=dict)
async def convert_to_invoice(
    quote_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    quote = await db.scalar(
        select(Quote)
        .where(Quote.id == quote_id, Quote.tenant_id == user.tenant_id)
        .options(selectinload(Quote.items))
    )
    if not quote:
        raise HTTPException(404, "Devis introuvable")
    if quote.converted_invoice_id:
        raise HTTPException(400, "Déjà converti")

    issue_date = date.today()
    due_date = issue_date + timedelta(days=30)
    number = await next_number(db, user.tenant_id, "invoice", issue_date)

    invoice = Invoice(
        tenant_id=user.tenant_id,
        number=number,
        client_id=quote.client_id,
        quote_id=quote.id,
        status="issued",
        issue_date=issue_date,
        due_date=due_date,
        currency=quote.currency,
        locale=quote.locale,
        subtotal_ht=quote.subtotal_ht,
        total_tax=quote.total_tax,
        total_ttc=quote.total_ttc,
        amount_paid=Decimal(0),
        amount_due=quote.total_ttc,
        notes_public=quote.notes_public,
        footer=quote.footer,
    )
    db.add(invoice)
    await db.flush()

    for q_it in quote.items:
        db.add(
            InvoiceItem(
                tenant_id=user.tenant_id,
                invoice_id=invoice.id,
                product_id=q_it.product_id,
                position=q_it.position,
                description=q_it.description,
                quantity=q_it.quantity,
                unit=q_it.unit,
                unit_price=q_it.unit_price,
                discount_rate=q_it.discount_rate,
                tax_rate=q_it.tax_rate,
                line_subtotal_ht=q_it.line_subtotal_ht,
                line_tax=q_it.line_tax,
                line_total_ttc=q_it.line_total_ttc,
            )
        )
    quote.converted_invoice_id = invoice.id
    quote.status = "converted"
    await db.commit()
    return {"invoice_id": invoice.id, "invoice_number": invoice.number}


@router.post("/{quote_id}/send")
async def send_quote(
    quote_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    quote = await db.scalar(
        select(Quote)
        .where(Quote.id == quote_id, Quote.tenant_id == user.tenant_id)
        .options(selectinload(Quote.items))
    )
    if not quote:
        raise HTTPException(404, "Devis introuvable")
    client = await db.scalar(select(Client).where(Client.id == quote.client_id))
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    if not client or not client.email:
        raise HTTPException(400, "Pas d'email pour ce client")

    out = storage_pdf_path("quote", user.tenant_id, quote.number)
    await render_pdf(
        "pdf/quote.html",
        {"quote": quote, "client": client, "tenant": tenant, "settings": settings},
        out,
    )
    quote.pdf_path = str(out)

    subject = f"Devis {quote.number} — {tenant.name}"
    body = (
        f"Bonjour,\n\nVeuillez trouver ci-joint le devis {quote.number} "
        f"d'un montant de {quote.total_ttc} {quote.currency}.\n\n"
        f"Valable jusqu'au {quote.valid_until.isoformat()}.\n\nCordialement,\n{tenant.name}"
    )
    await send_email(to=client.email, subject=subject, body_text=body, attachments=[Path(str(out))])

    if quote.status == "draft":
        quote.status = "sent"
    await db.commit()
    return {"sent_to": client.email}


@router.get("/{quote_id}/pdf", response_class=FileResponse)
async def download_pdf(
    quote_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    quote = await db.scalar(
        select(Quote)
        .where(Quote.id == quote_id, Quote.tenant_id == user.tenant_id)
        .options(selectinload(Quote.items))
    )
    if not quote:
        raise HTTPException(404, "Devis introuvable")
    client = await db.scalar(select(Client).where(Client.id == quote.client_id))
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))

    out = storage_pdf_path("quote", user.tenant_id, quote.number)
    await render_pdf(
        "pdf/quote.html",
        {"quote": quote, "client": client, "tenant": tenant, "settings": settings},
        out,
    )
    quote.pdf_path = str(out)
    await db.commit()
    return FileResponse(quote.pdf_path, filename=f"{quote.number}.pdf", media_type="application/pdf")
