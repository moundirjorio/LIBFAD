"""Espace client (portail) — interface distincte de l'interface entreprise.

Un client se connecte sur `/portail` avec email + mot de passe (compte
`ClientPortalUser`) et ne voit QUE ses propres factures, devis et paiements.
Aucune donnée d'un autre client ou de la gestion interne n'est accessible.
"""

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.deps import PORTAL_COOKIE, get_optional_portal_user, get_session
from app.i18n import date_fr, datetime_fr, money, number
from app.models.client import Client
from app.models.client_portal import ClientPortalUser
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.quote import Quote
from app.models.tenant import Tenant
from app.ratelimit import is_blocked, record_failure, reset as reset_ratelimit
from app.schemas.types import normalize_email
from app.security import create_access_token, hash_password, verify_password
from app.services.email import send_email
from app.services.password_reset import consume, create_token, verify_token
from app.services.pdf import render_pdf, storage_pdf_path

router = APIRouter(tags=["portail"])

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["money"] = money
templates.env.filters["num"] = number
templates.env.filters["date_fr"] = date_fr
templates.env.filters["datetime_fr"] = datetime_fr


def _ctx(request: Request, portal_user: ClientPortalUser | None = None, **extra) -> dict:
    return {
        "request": request,
        "settings": settings,
        "portal_user": portal_user,
        "client": getattr(portal_user, "client", None) if portal_user else None,
        **extra,
    }


async def _require_portal_user(
    portal_user: ClientPortalUser | None,
) -> ClientPortalUser | RedirectResponse:
    """Renvoie l'utilisateur portail ou une redirection vers le login."""
    if not portal_user:
        return RedirectResponse(url="/portail/login")
    return portal_user


# --- Authentification -------------------------------------------------------
@router.get("/portail/login", response_class=HTMLResponse)
async def portal_login_page(
    request: Request,
    portal_user: ClientPortalUser | None = Depends(get_optional_portal_user),
):
    if portal_user:
        return RedirectResponse(url="/portail")
    return templates.TemplateResponse("portal/login.html", _ctx(request))


@router.post("/portail/login", response_class=HTMLResponse)
async def portal_login_form(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    rl_key = f"portal-login:{request.client.host if request.client else '?'}:{normalize_email(email)}"
    if is_blocked(rl_key):
        return templates.TemplateResponse(
            "portal/login.html",
            _ctx(request, error="Trop de tentatives. Réessayez dans quelques minutes."),
            status_code=429,
        )

    user = await db.scalar(
        select(ClientPortalUser)
        .where(ClientPortalUser.email == normalize_email(email))
        .options(selectinload(ClientPortalUser.client))
    )
    if not user or not verify_password(password, user.password_hash):
        record_failure(rl_key)
        return templates.TemplateResponse(
            "portal/login.html",
            _ctx(request, error="Email ou mot de passe incorrect"),
            status_code=401,
        )
    if not user.is_active:
        return templates.TemplateResponse(
            "portal/login.html",
            _ctx(request, error="Accès désactivé. Contactez votre fournisseur."),
            status_code=403,
        )
    from app.models.base import utcnow

    reset_ratelimit(rl_key)
    user.last_login_at = utcnow()
    await db.commit()

    token = create_access_token(
        subject=str(user.id),
        extra={"typ": "portal", "cid": user.client_id, "tid": user.tenant_id},
    )
    resp = RedirectResponse(url="/portail", status_code=302)
    resp.set_cookie(
        key=PORTAL_COOKIE,
        value=token,
        max_age=settings.jwt_expires_minutes * 60,
        httponly=True,
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
    )
    return resp


@router.get("/portail/logout")
async def portal_logout():
    resp = RedirectResponse(url="/portail/login", status_code=302)
    resp.delete_cookie(PORTAL_COOKIE)
    return resp


# --- Mot de passe oublié (comptes portail) ----------------------------------
@router.get("/portail/forgot-password", response_class=HTMLResponse)
async def portal_forgot_page(request: Request):
    return templates.TemplateResponse("portal/forgot.html", _ctx(request))


@router.post("/portail/forgot-password", response_class=HTMLResponse)
async def portal_forgot_form(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    user = await db.scalar(
        select(ClientPortalUser).where(ClientPortalUser.email == normalize_email(email))
    )
    if user and user.is_active:
        token = await create_token(db, "portal", user.id)
        await db.commit()
        link = f"{settings.base_url.rstrip('/')}/portail/reset-password?token={token}"
        await send_email(
            to=user.email,
            subject="Réinitialisation de votre mot de passe — Espace client",
            body_text=(
                f"Bonjour,\n\nVous avez demandé à réinitialiser le mot de passe de votre espace client.\n"
                f"Cliquez sur ce lien (valable 1 heure) :\n{link}\n\n"
                f"Si vous n'êtes pas à l'origine de cette demande, ignorez cet email.\n"
            ),
        )
    return templates.TemplateResponse("portal/forgot.html", _ctx(request, sent=True))


@router.get("/portail/reset-password", response_class=HTMLResponse)
async def portal_reset_page(request: Request, token: str = "", db: AsyncSession = Depends(get_session)):
    valid = bool(await verify_token(db, "portal", token)) if token else False
    return templates.TemplateResponse("portal/reset.html", _ctx(request, token=token, valid=valid))


@router.post("/portail/reset-password", response_class=HTMLResponse)
async def portal_reset_form(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    row = await verify_token(db, "portal", token)
    if not row:
        return templates.TemplateResponse(
            "portal/reset.html",
            _ctx(request, token=token, valid=False, error="Lien invalide ou expiré. Refaites une demande."),
            status_code=400,
        )
    if len(password) < 8:
        return templates.TemplateResponse(
            "portal/reset.html",
            _ctx(request, token=token, valid=True, error="Le mot de passe doit faire au moins 8 caractères."),
            status_code=400,
        )
    user = await db.scalar(select(ClientPortalUser).where(ClientPortalUser.id == row.subject_id))
    if user:
        user.password_hash = hash_password(password)
    consume(row)
    await db.commit()
    return templates.TemplateResponse(
        "portal/login.html",
        _ctx(request, info="Mot de passe mis à jour. Vous pouvez vous connecter."),
    )


# --- Tableau de bord --------------------------------------------------------
@router.get("/portail", response_class=HTMLResponse)
async def portal_home(
    request: Request,
    portal_user: ClientPortalUser | None = Depends(get_optional_portal_user),
    db: AsyncSession = Depends(get_session),
):
    guard = await _require_portal_user(portal_user)
    if isinstance(guard, RedirectResponse):
        return guard

    cid = portal_user.client_id

    # KPIs : total dû, en retard, nb devis en attente
    total_due = await db.scalar(
        select(func.coalesce(func.sum(Invoice.amount_due), 0)).where(
            Invoice.client_id == cid,
            Invoice.status.in_(["issued", "sent", "partially_paid", "overdue"]),
        )
    )
    overdue_due = await db.scalar(
        select(func.coalesce(func.sum(Invoice.amount_due), 0)).where(
            Invoice.client_id == cid, Invoice.status == "overdue"
        )
    )
    pending_quotes = await db.scalar(
        select(func.count(Quote.id)).where(
            Quote.client_id == cid, Quote.status.in_(["sent", "draft"])
        )
    )

    recent_invoices = list(
        await db.scalars(
            select(Invoice)
            .where(Invoice.client_id == cid, Invoice.status != "draft")
            .order_by(Invoice.issue_date.desc(), Invoice.id.desc())
            .limit(6)
        )
    )

    return templates.TemplateResponse(
        "portal/dashboard.html",
        _ctx(
            request,
            portal_user,
            total_due=total_due,
            overdue_due=overdue_due,
            pending_quotes=pending_quotes,
            recent_invoices=recent_invoices,
        ),
    )


# --- Factures ---------------------------------------------------------------
@router.get("/portail/factures", response_class=HTMLResponse)
async def portal_invoices(
    request: Request,
    portal_user: ClientPortalUser | None = Depends(get_optional_portal_user),
    db: AsyncSession = Depends(get_session),
):
    guard = await _require_portal_user(portal_user)
    if isinstance(guard, RedirectResponse):
        return guard

    invoices = list(
        await db.scalars(
            select(Invoice)
            .where(Invoice.client_id == portal_user.client_id, Invoice.status != "draft")
            .order_by(Invoice.issue_date.desc(), Invoice.id.desc())
        )
    )
    return templates.TemplateResponse(
        "portal/invoices.html", _ctx(request, portal_user, invoices=invoices)
    )


async def _get_client_invoice(db: AsyncSession, portal_user: ClientPortalUser, invoice_id: int):
    """Charge une facture en garantissant qu'elle appartient bien au client connecté."""
    invoice = await db.scalar(
        select(Invoice)
        .where(
            Invoice.id == invoice_id,
            Invoice.client_id == portal_user.client_id,
            Invoice.status != "draft",
        )
        .options(selectinload(Invoice.items), selectinload(Invoice.payments))
    )
    return invoice


@router.get("/portail/factures/{invoice_id}", response_class=HTMLResponse)
async def portal_invoice_detail(
    invoice_id: int,
    request: Request,
    portal_user: ClientPortalUser | None = Depends(get_optional_portal_user),
    db: AsyncSession = Depends(get_session),
):
    guard = await _require_portal_user(portal_user)
    if isinstance(guard, RedirectResponse):
        return guard

    invoice = await _get_client_invoice(db, portal_user, invoice_id)
    if not invoice:
        return RedirectResponse(url="/portail/factures")
    return templates.TemplateResponse(
        "portal/invoice_detail.html",
        _ctx(request, portal_user, invoice=invoice, client=portal_user.client),
    )


@router.get("/portail/factures/{invoice_id}/regler", response_class=HTMLResponse)
async def portal_invoice_pay(
    invoice_id: int,
    request: Request,
    portal_user: ClientPortalUser | None = Depends(get_optional_portal_user),
    db: AsyncSession = Depends(get_session),
):
    guard = await _require_portal_user(portal_user)
    if isinstance(guard, RedirectResponse):
        return guard

    invoice = await _get_client_invoice(db, portal_user, invoice_id)
    if not invoice:
        return RedirectResponse(url="/portail/factures")
    tenant = await db.scalar(select(Tenant).where(Tenant.id == portal_user.tenant_id))
    return templates.TemplateResponse(
        "portal/invoice_pay.html",
        _ctx(request, portal_user, invoice=invoice, tenant=tenant, client=portal_user.client),
    )


@router.get("/portail/factures/{invoice_id}/pdf", response_class=FileResponse)
async def portal_invoice_pdf(
    invoice_id: int,
    portal_user: ClientPortalUser | None = Depends(get_optional_portal_user),
    db: AsyncSession = Depends(get_session),
):
    if not portal_user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Non authentifié")
    invoice = await _get_client_invoice(db, portal_user, invoice_id)
    if not invoice:
        raise HTTPException(404, "Facture introuvable")

    client = portal_user.client
    tenant = await db.scalar(select(Tenant).where(Tenant.id == portal_user.tenant_id))
    out = storage_pdf_path("invoice", portal_user.tenant_id, invoice.number)
    await render_pdf(
        "pdf/invoice.html",
        {"invoice": invoice, "client": client, "tenant": tenant, "settings": settings},
        out,
    )
    invoice.pdf_path = str(out)
    await db.commit()
    return FileResponse(out, filename=f"{invoice.number}.pdf", media_type="application/pdf")


# --- Devis ------------------------------------------------------------------
@router.get("/portail/devis", response_class=HTMLResponse)
async def portal_quotes(
    request: Request,
    portal_user: ClientPortalUser | None = Depends(get_optional_portal_user),
    db: AsyncSession = Depends(get_session),
):
    guard = await _require_portal_user(portal_user)
    if isinstance(guard, RedirectResponse):
        return guard

    quotes = list(
        await db.scalars(
            select(Quote)
            .where(Quote.client_id == portal_user.client_id, Quote.status != "draft")
            .order_by(Quote.issue_date.desc(), Quote.id.desc())
        )
    )
    return templates.TemplateResponse(
        "portal/quotes.html", _ctx(request, portal_user, quotes=quotes)
    )


async def _get_client_quote(db: AsyncSession, portal_user: ClientPortalUser, quote_id: int):
    return await db.scalar(
        select(Quote)
        .where(
            Quote.id == quote_id,
            Quote.client_id == portal_user.client_id,
            Quote.status != "draft",
        )
        .options(selectinload(Quote.items))
    )


@router.get("/portail/devis/{quote_id}", response_class=HTMLResponse)
async def portal_quote_detail(
    quote_id: int,
    request: Request,
    portal_user: ClientPortalUser | None = Depends(get_optional_portal_user),
    db: AsyncSession = Depends(get_session),
):
    guard = await _require_portal_user(portal_user)
    if isinstance(guard, RedirectResponse):
        return guard

    quote = await _get_client_quote(db, portal_user, quote_id)
    if not quote:
        return RedirectResponse(url="/portail/devis")
    return templates.TemplateResponse(
        "portal/quote_detail.html",
        _ctx(request, portal_user, quote=quote, client=portal_user.client),
    )


@router.post("/portail/devis/{quote_id}/decision", response_class=HTMLResponse)
async def portal_quote_decision(
    quote_id: int,
    request: Request,
    decision: str = Form(...),  # "accept" | "refuse"
    portal_user: ClientPortalUser | None = Depends(get_optional_portal_user),
    db: AsyncSession = Depends(get_session),
):
    if not portal_user:
        return RedirectResponse(url="/portail/login", status_code=302)
    quote = await _get_client_quote(db, portal_user, quote_id)
    if not quote:
        return RedirectResponse(url="/portail/devis", status_code=302)

    # On n'autorise la décision que sur un devis encore ouvert.
    if quote.status in ("sent", "expired"):
        if decision == "accept":
            quote.status = "accepted"
            quote.accepted_at = date.today()
        elif decision == "refuse":
            quote.status = "refused"
        await db.commit()

    return RedirectResponse(url=f"/portail/devis/{quote_id}", status_code=303)


# --- Paiements --------------------------------------------------------------
@router.get("/portail/paiements", response_class=HTMLResponse)
async def portal_payments(
    request: Request,
    portal_user: ClientPortalUser | None = Depends(get_optional_portal_user),
    db: AsyncSession = Depends(get_session),
):
    guard = await _require_portal_user(portal_user)
    if isinstance(guard, RedirectResponse):
        return guard

    cid = portal_user.client_id
    # Paiements de toutes les factures du client (jointure sur invoice.client_id)
    rows = list(
        await db.scalars(
            select(Payment)
            .join(Invoice, Payment.invoice_id == Invoice.id)
            .where(Invoice.client_id == cid)
            .order_by(Payment.paid_on.desc(), Payment.id.desc())
        )
    )
    invoices_by_id = {
        inv.id: inv
        for inv in await db.scalars(select(Invoice).where(Invoice.client_id == cid))
    }
    total_paid = sum((p.amount for p in rows), 0)
    total_due = await db.scalar(
        select(func.coalesce(func.sum(Invoice.amount_due), 0)).where(
            Invoice.client_id == cid,
            Invoice.status.in_(["issued", "sent", "partially_paid", "overdue"]),
        )
    )
    return templates.TemplateResponse(
        "portal/payments.html",
        _ctx(
            request,
            portal_user,
            payments=rows,
            invoices_by_id=invoices_by_id,
            total_paid=total_paid,
            total_due=total_due,
        ),
    )


# --- Profil -----------------------------------------------------------------
@router.get("/portail/profil", response_class=HTMLResponse)
async def portal_profile(
    request: Request,
    portal_user: ClientPortalUser | None = Depends(get_optional_portal_user),
    db: AsyncSession = Depends(get_session),
):
    guard = await _require_portal_user(portal_user)
    if isinstance(guard, RedirectResponse):
        return guard
    return templates.TemplateResponse(
        "portal/profile.html", _ctx(request, portal_user, client=portal_user.client)
    )


@router.post("/portail/profil", response_class=HTMLResponse)
async def portal_profile_update(
    request: Request,
    phone: str | None = Form(None),
    website: str | None = Form(None),
    billing_address_line1: str | None = Form(None),
    billing_address_line2: str | None = Form(None),
    billing_postal_code: str | None = Form(None),
    billing_city: str | None = Form(None),
    portal_user: ClientPortalUser | None = Depends(get_optional_portal_user),
    db: AsyncSession = Depends(get_session),
):
    guard = await _require_portal_user(portal_user)
    if isinstance(guard, RedirectResponse):
        return guard

    client = await db.scalar(select(Client).where(Client.id == portal_user.client_id))
    if client:
        client.phone = phone
        client.website = website
        client.billing_address_line1 = billing_address_line1
        client.billing_address_line2 = billing_address_line2
        client.billing_postal_code = billing_postal_code
        client.billing_city = billing_city
        await db.commit()

    return RedirectResponse(url="/portail/profil?ok=1", status_code=303)
