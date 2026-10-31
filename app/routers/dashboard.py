"""Pages HTML principales (dashboard, listes, formulaires)."""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.deps import get_optional_user, get_session
from app.i18n import date_fr, datetime_fr, money, number
from app.models.client import Client
from app.models.dunning import DunningMessage, DunningRun, DunningScenario
from app.models.invoice import Invoice
from app.models.quote import Quote
from app.models.user import User
from app.services.reports import dashboard_kpis

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["money"] = money
templates.env.filters["num"] = number
templates.env.filters["date_fr"] = date_fr
templates.env.filters["datetime_fr"] = datetime_fr


async def _bank_context(db: AsyncSession, tenant_id: int):
    """Comptes bancaires du tenant + id du compte par défaut (pour pré-sélection)."""
    from app.models.bank import BankAccount
    from app.models.tenant import Tenant

    accounts = list(await db.scalars(
        select(BankAccount).where(BankAccount.tenant_id == tenant_id).order_by(BankAccount.name)
    ))
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    default_id = getattr(tenant, "default_bank_account_id", None)
    return accounts, default_id


def _whatsapp_number(phone: str | None, region: str | None) -> str | None:
    """Formate un numéro en international sans '+' (pour les liens wa.me)."""
    if not phone:
        return None
    try:
        import phonenumbers

        parsed = phonenumbers.parse(phone, (region or "MA").upper())
        if not phonenumbers.is_valid_number(parsed):
            return None
        return phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164
        ).lstrip("+")
    except Exception:
        digits = "".join(c for c in phone if c.isdigit())
        if digits.startswith("0"):
            digits = "212" + digits[1:]  # Maroc par défaut
        return digits or None


def _ctx(request: Request, user: User | None = None, **extra) -> dict:
    return {
        "request": request,
        "settings": settings,
        "current_user": user,
        "current_tenant_id": user.tenant_id if user else None,
        **extra,
    }


@router.get("/vitrine", response_class=HTMLResponse)
async def vitrine(request: Request):
    """Page vitrine, accessible à tout moment (même connecté)."""
    from app.services.plans import PLANS

    return templates.TemplateResponse(
        "landing.html",
        {"request": request, "settings": settings, "plans": list(PLANS.values())},
    )


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        from app.services.plans import PLANS

        return templates.TemplateResponse(
            "landing.html",
            {"request": request, "settings": settings, "plans": list(PLANS.values())},
        )
    _ = user  # connecté : on continue vers le tableau de bord ci-dessous
    kpis = await dashboard_kpis(db, user.tenant_id)

    recent_invoices = list(
        await db.scalars(
            select(Invoice)
            .where(Invoice.tenant_id == user.tenant_id)
            .order_by(Invoice.issue_date.desc(), Invoice.id.desc())
            .limit(8)
        )
    )
    overdue = list(
        await db.scalars(
            select(Invoice)
            .where(Invoice.tenant_id == user.tenant_id)
            .where(Invoice.status == "overdue")
            .order_by(Invoice.due_date.asc())
            .limit(8)
        )
    )

    # --- Checklist de démarrage (onboarding) ---
    from sqlalchemy import func

    from app.models.tenant import Tenant

    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    n_clients = await db.scalar(select(func.count(Client.id)).where(Client.tenant_id == user.tenant_id))
    n_invoices = await db.scalar(select(func.count(Invoice.id)).where(Invoice.tenant_id == user.tenant_id))
    steps = [
        ("Compléter les infos de votre société", bool(tenant and tenant.legal_name and tenant.address_line1
            and (tenant.ice or tenant.vat_number)), "/settings"),
        ("Ajouter votre logo", bool(tenant and tenant.logo_path), "/settings"),
        ("Créer un premier client", (n_clients or 0) > 0, "/clients"),
        ("Émettre une première facture", (n_invoices or 0) > 0, "/invoices/new"),
    ]
    onboarding = {
        "steps": steps,
        "done": sum(1 for _, ok, _ in steps if ok),
        "total": len(steps),
    }

    return templates.TemplateResponse(
        "dashboard/index.html",
        _ctx(request, user, kpis=kpis, recent_invoices=recent_invoices, overdue=overdue,
             onboarding=onboarding, currency=(tenant.currency if tenant else "EUR")),
    )


@router.get("/clients", response_class=HTMLResponse)
async def clients_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    clients = list(
        await db.scalars(
            select(Client).where(Client.tenant_id == user.tenant_id).order_by(Client.name)
        )
    )
    return templates.TemplateResponse(
        "clients/list.html", _ctx(request, user, clients=clients)
    )


@router.get("/clients/{client_id}", response_class=HTMLResponse)
async def client_detail(
    client_id: int,
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    client = await db.scalar(
        select(Client)
        .where(Client.id == client_id, Client.tenant_id == user.tenant_id)
        .options(selectinload(Client.contacts))
    )
    if not client:
        return RedirectResponse(url="/clients")
    invoices = list(
        await db.scalars(
            select(Invoice)
            .where(Invoice.tenant_id == user.tenant_id, Invoice.client_id == client_id)
            .order_by(Invoice.issue_date.desc())
        )
    )
    from app.models.client_portal import ClientPortalUser

    portal_user = await db.scalar(
        select(ClientPortalUser).where(ClientPortalUser.client_id == client_id)
    )
    # Relance WhatsApp : numéro + factures impayées + total restant dû
    unpaid = [i for i in invoices
              if i.status in ("sent", "issued", "overdue", "partially_paid")
              and float(i.amount_due or 0) > 0]
    total_due = sum(float(i.amount_due or 0) for i in unpaid)
    wa_number = _whatsapp_number(client.phone, client.billing_country)
    return templates.TemplateResponse(
        "clients/detail.html",
        _ctx(request, user, client=client, invoices=invoices, portal_user=portal_user,
             unpaid_invoices=unpaid, total_due=total_due, wa_number=wa_number),
    )


@router.get("/invoices", response_class=HTMLResponse)
async def invoices_page(
    request: Request,
    status: str | None = None,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    UNPAID_STATUSES = ("sent", "issued", "overdue", "partially_paid")
    stmt = select(Invoice).where(Invoice.tenant_id == user.tenant_id).order_by(Invoice.issue_date.desc(), Invoice.id.desc())
    if status == "unpaid":
        # « À encaisser » : toutes les factures non soldées (reste dû > 0)
        stmt = stmt.where(Invoice.status.in_(UNPAID_STATUSES), Invoice.amount_due > 0)
    elif status:
        stmt = stmt.where(Invoice.status == status)
    invoices = list(await db.scalars(stmt))
    clients = {c.id: c for c in await db.scalars(select(Client).where(Client.tenant_id == user.tenant_id))}
    total_due = sum(float(i.amount_due or 0) for i in invoices)
    return templates.TemplateResponse(
        "invoices/list.html",
        _ctx(request, user, invoices=invoices, clients=clients, current_status=status,
             total_due=total_due),
    )


@router.get("/invoices/new", response_class=HTMLResponse)
async def invoice_new(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from app.models.tenant import Tenant
    clients = list(await db.scalars(select(Client).where(Client.tenant_id == user.tenant_id).order_by(Client.name)))
    bank_accounts, default_bank = await _bank_context(db, user.tenant_id)
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    return templates.TemplateResponse(
        "invoices/edit.html", _ctx(request, user, invoice=None, clients=clients,
                                   bank_accounts=bank_accounts, default_bank=default_bank,
                                   default_locale=getattr(tenant, "locale", "fr_FR"))
    )


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
async def invoice_view(
    invoice_id: int,
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    invoice = await db.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.tenant_id == user.tenant_id)
        .options(selectinload(Invoice.items), selectinload(Invoice.payments))
    )
    if not invoice:
        return RedirectResponse(url="/invoices")
    client = await db.scalar(select(Client).where(Client.id == invoice.client_id))
    bank_accounts, _ = await _bank_context(db, user.tenant_id)
    wa_number = _whatsapp_number(client.phone if client else None,
                                 client.billing_country if client else None)
    return templates.TemplateResponse(
        "invoices/detail.html", _ctx(request, user, invoice=invoice, client=client,
                                     bank_accounts=bank_accounts, wa_number=wa_number)
    )


@router.get("/quotes", response_class=HTMLResponse)
async def quotes_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    quotes = list(
        await db.scalars(
            select(Quote)
            .where(Quote.tenant_id == user.tenant_id)
            .order_by(Quote.issue_date.desc(), Quote.id.desc())
        )
    )
    clients = {c.id: c for c in await db.scalars(select(Client).where(Client.tenant_id == user.tenant_id))}
    return templates.TemplateResponse(
        "quotes/list.html", _ctx(request, user, quotes=quotes, clients=clients)
    )


@router.get("/quotes/new", response_class=HTMLResponse)
async def quote_new(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    clients = list(
        await db.scalars(
            select(Client).where(Client.tenant_id == user.tenant_id).order_by(Client.name)
        )
    )
    from app.models.tenant import Tenant
    bank_accounts, default_bank = await _bank_context(db, user.tenant_id)
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    return templates.TemplateResponse(
        "quotes/edit.html", _ctx(request, user, quote=None, clients=clients,
                                 bank_accounts=bank_accounts, default_bank=default_bank,
                                 default_locale=getattr(tenant, "locale", "fr_FR"))
    )


@router.get("/quotes/{quote_id}", response_class=HTMLResponse)
async def quote_view(
    quote_id: int,
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    quote = await db.scalar(
        select(Quote)
        .where(Quote.id == quote_id, Quote.tenant_id == user.tenant_id)
        .options(selectinload(Quote.items))
    )
    if not quote:
        return RedirectResponse(url="/quotes")
    client = await db.scalar(select(Client).where(Client.id == quote.client_id))
    return templates.TemplateResponse(
        "quotes/detail.html", _ctx(request, user, quote=quote, client=client)
    )


@router.get("/dunning", response_class=HTMLResponse)
async def dunning_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    scenarios = list(
        await db.scalars(
            select(DunningScenario)
            .where(DunningScenario.tenant_id == user.tenant_id)
            .options(selectinload(DunningScenario.steps))
        )
    )
    runs = list(
        await db.scalars(
            select(DunningRun)
            .where(DunningRun.tenant_id == user.tenant_id)
            .where(DunningRun.status.in_(["running", "paused"]))
            .order_by(DunningRun.next_action_at.asc())
        )
    )
    messages = list(
        await db.scalars(
            select(DunningMessage)
            .where(DunningMessage.tenant_id == user.tenant_id)
            .order_by(DunningMessage.created_at.desc())
            .limit(20)
        )
    )
    return templates.TemplateResponse(
        "dunning/index.html", _ctx(request, user, scenarios=scenarios, runs=runs, messages=messages)
    )


@router.get("/telegram", response_class=HTMLResponse)
async def telegram_page(
    request: Request,
    m: str | None = None,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    """Configuration du bot Telegram de la société."""
    if not user:
        return RedirectResponse(url="/login")
    from datetime import UTC, datetime

    from app.models.telegram import TelegramLink
    from app.models.tenant import Tenant
    from app.routers.telegram import MESSAGES, can_manage
    from app.services import assistant_agent, telegram_bot

    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    token = tenant.telegram_bot_token
    rows = (await db.execute(
        select(TelegramLink, User.full_name, User.email)
        .join(User, User.id == TelegramLink.user_id)
        .where(TelegramLink.tenant_id == tenant.id, TelegramLink.chat_id.is_not(None))
        .order_by(TelegramLink.linked_at.desc())
    )).all()
    pending = await db.scalar(
        select(TelegramLink).where(
            TelegramLink.tenant_id == tenant.id, TelegramLink.user_id == user.id,
            TelegramLink.chat_id.is_(None), TelegramLink.code_expires_at > datetime.now(UTC))
    )
    return templates.TemplateResponse(
        "telegram/index.html",
        _ctx(request, user, tenant=tenant,
             bot_username=tenant.telegram_bot_username,
             token_hint=(f"{token.split(':')[0]}:…{token[-4:]}" if token else None),
             status=telegram_bot.poller_status(tenant.id) if token else None,
             links=[{"link": link, "name": name, "email": email} for link, name, email in rows],
             my_link=any(link.user_id == user.id for link, _, _ in rows),
             pending=pending, can_manage=can_manage(user),
             ai_configured=assistant_agent.is_configured(),
             commands=telegram_bot.BOT_COMMANDS, flash=MESSAGES.get(m or "")),
    )


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
):
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("chat/index.html", _ctx(request, user))


# Comptes comptables d'achat courants (CGNC Maroc) proposés en autocomplétion.
PURCHASE_ACCOUNTS = [
    ("6111", "Achats de marchandises"),
    ("6121", "Achats de matières premières"),
    ("61251", "Achats de fournitures non stockables"),
    ("61256", "Achats de fournitures de bureau"),
    ("61331", "Locations et charges locatives"),
    ("61425", "Transports et déplacements"),
    ("61441", "Publicité, publications"),
    ("61451", "Frais postaux et de télécommunications"),
    ("61361", "Honoraires et rémunérations d'intermédiaires"),
    ("6167", "Impôts, taxes et droits assimilés"),
    ("61255", "Achats de petit outillage et matériel"),
    ("6135", "Rémunérations de personnel extérieur"),
]


@router.get("/achats", response_class=HTMLResponse)
async def purchases_page(
    request: Request,
    kind: str = "receipt",
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from sqlalchemy import func

    from app.models.purchase_receipt import PurchaseReceipt

    if kind not in ("receipt", "expense", "expense_report"):
        kind = "receipt"
    receipts = list(
        await db.scalars(
            select(PurchaseReceipt)
            .where(PurchaseReceipt.tenant_id == user.tenant_id, PurchaseReceipt.kind == kind)
            .order_by(PurchaseReceipt.deposit_date.desc(), PurchaseReceipt.id.desc())
        )
    )
    # Compteurs par onglet
    counts = {
        k: (await db.scalar(
            select(func.count(PurchaseReceipt.id)).where(
                PurchaseReceipt.tenant_id == user.tenant_id, PurchaseReceipt.kind == k
            )
        )) or 0
        for k in ("receipt", "expense", "expense_report")
    }
    total_ttc = sum((float(r.amount_ttc or 0) for r in receipts), 0.0)
    total_tva = sum((float(r.amount_tva or 0) for r in receipts), 0.0)
    bank_accounts, default_bank = await _bank_context(db, user.tenant_id)
    banks_by_id = {a.id: a for a in bank_accounts}
    from app.models.supplier import Supplier

    suppliers = list(await db.scalars(
        select(Supplier).where(Supplier.tenant_id == user.tenant_id).order_by(Supplier.name)
    ))
    return templates.TemplateResponse(
        "accounting/purchases.html",
        _ctx(request, user, receipts=receipts, current_kind=kind, counts=counts,
             accounts=PURCHASE_ACCOUNTS, total_ttc=total_ttc, total_tva=total_tva,
             bank_accounts=bank_accounts, banks_by_id=banks_by_id, default_bank=default_bank,
             suppliers=suppliers),
    )


async def _ledger_ready(db, tenant_id):
    """Garantit que le plan de comptes/journaux existe (auto-seed au 1er accès)."""
    from sqlalchemy import func

    from app.models.ledger import LedgerAccount
    from app.services.accounting import seed_ledger

    n = await db.scalar(select(func.count(LedgerAccount.id)).where(LedgerAccount.tenant_id == tenant_id))
    if not n:
        await seed_ledger(db, tenant_id)


@router.get("/banque", response_class=HTMLResponse)
async def bank_page(
    request: Request,
    account: int | None = None,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from app.models.bank import BankAccount, BankTransaction
    from app.services.bank import treasury_summary

    accounts = list(await db.scalars(
        select(BankAccount).where(BankAccount.tenant_id == user.tenant_id).order_by(BankAccount.name)
    ))
    selected = None
    transactions = []
    if accounts:
        selected = next((a for a in accounts if a.id == account), accounts[0])
        transactions = list(await db.scalars(
            select(BankTransaction)
            .where(BankTransaction.account_id == selected.id)
            .order_by(BankTransaction.op_date.desc(), BankTransaction.id.desc())
            .limit(200)
        ))
    summary = await treasury_summary(db, user.tenant_id)
    from app.models.ledger import LedgerAccount

    await _ledger_ready(db, user.tenant_id)
    ledger_accounts = list(await db.scalars(
        select(LedgerAccount).where(LedgerAccount.tenant_id == user.tenant_id).order_by(LedgerAccount.code)
    ))
    return templates.TemplateResponse(
        "accounting/bank.html",
        _ctx(request, user, accounts=accounts, selected=selected,
             transactions=transactions, summary=summary, ledger_accounts=ledger_accounts),
    )


@router.get("/banque/rapprochement", response_class=HTMLResponse)
async def bank_reconciliation_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from app.services.bank import reconciliation_suggestions

    suggestions = await reconciliation_suggestions(db, user.tenant_id)
    matched = sum(1 for s in suggestions if s["invoice"])
    return templates.TemplateResponse(
        "accounting/reconciliation.html",
        _ctx(request, user, suggestions=suggestions, matched=matched),
    )


@router.get("/comptabilite/plan", response_class=HTMLResponse)
async def ledger_plan_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from app.models.ledger import LedgerAccount

    await _ledger_ready(db, user.tenant_id)
    accounts = list(await db.scalars(
        select(LedgerAccount).where(LedgerAccount.tenant_id == user.tenant_id).order_by(LedgerAccount.code)
    ))
    return templates.TemplateResponse("accounting/plan.html", _ctx(request, user, accounts=accounts))


@router.get("/comptabilite/ecritures", response_class=HTMLResponse)
async def ledger_entries_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from sqlalchemy.orm import selectinload

    from app.models.ledger import Journal, JournalEntry, JournalEntryLine, LedgerAccount

    await _ledger_ready(db, user.tenant_id)
    entries = list(await db.scalars(
        select(JournalEntry).where(JournalEntry.tenant_id == user.tenant_id)
        .options(selectinload(JournalEntry.lines).selectinload(JournalEntryLine.account))
        .order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc()).limit(50)
    ))
    journals = list(await db.scalars(select(Journal).where(Journal.tenant_id == user.tenant_id).order_by(Journal.code)))
    accounts = list(await db.scalars(select(LedgerAccount).where(LedgerAccount.tenant_id == user.tenant_id).order_by(LedgerAccount.code)))
    journals_by_id = {j.id: j for j in journals}
    return templates.TemplateResponse(
        "accounting/entries.html",
        _ctx(request, user, entries=entries, journals=journals, accounts=accounts, journals_by_id=journals_by_id),
    )


@router.get("/comptabilite/tva", response_class=HTMLResponse)
async def vat_page(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from datetime import date as _d

    from app.services.accounting import vat_return

    await _ledger_ready(db, user.tenant_id)
    today = _d.today()
    year = year or today.year
    if month is not None and month == 0:
        month = None
    vr = await vat_return(db, user.tenant_id, year, month)
    return templates.TemplateResponse(
        "accounting/tva.html",
        _ctx(request, user, vr=vr, year=year, month=month, current_year=today.year),
    )


@router.get("/comptabilite/etats", response_class=HTMLResponse)
async def ledger_statements_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from app.services.accounting import financial_statements

    await _ledger_ready(db, user.tenant_id)
    fs = await financial_statements(db, user.tenant_id)
    return templates.TemplateResponse("accounting/statements.html", _ctx(request, user, fs=fs))


@router.get("/comptabilite/grand-livre", response_class=HTMLResponse)
async def ledger_gl_page(
    request: Request,
    account: int | None = None,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from app.models.ledger import JournalEntryLine, LedgerAccount
    from app.services.accounting import general_ledger

    await _ledger_ready(db, user.tenant_id)
    # Comptes ayant des mouvements
    moved_ids = set(await db.scalars(
        select(JournalEntryLine.account_id).where(JournalEntryLine.tenant_id == user.tenant_id).distinct()
    ))
    accounts = list(await db.scalars(
        select(LedgerAccount).where(
            LedgerAccount.tenant_id == user.tenant_id, LedgerAccount.id.in_(moved_ids or {0})
        ).order_by(LedgerAccount.code)
    ))
    selected_id = account or (accounts[0].id if accounts else None)
    gl = await general_ledger(db, user.tenant_id, selected_id) if selected_id else {"account": None, "rows": []}
    return templates.TemplateResponse(
        "accounting/grand_livre.html",
        _ctx(request, user, accounts=accounts, selected_id=selected_id, gl=gl),
    )


@router.get("/comptabilite/balance", response_class=HTMLResponse)
async def ledger_balance_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from app.services.accounting import trial_balance

    await _ledger_ready(db, user.tenant_id)
    balance = await trial_balance(db, user.tenant_id)
    return templates.TemplateResponse("accounting/balance.html", _ctx(request, user, balance=balance))


@router.get("/fournisseurs", response_class=HTMLResponse)
async def suppliers_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from app.models.supplier import Supplier

    suppliers = list(await db.scalars(
        select(Supplier).where(Supplier.tenant_id == user.tenant_id).order_by(Supplier.name)
    ))
    return templates.TemplateResponse(
        "suppliers/list.html", _ctx(request, user, suppliers=suppliers, accounts=PURCHASE_ACCOUNTS)
    )


@router.get("/documents", response_class=HTMLResponse)
async def documents_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from app.models.document import Document

    docs = list(
        await db.scalars(
            select(Document).where(Document.tenant_id == user.tenant_id).order_by(Document.id.desc())
        )
    )
    return templates.TemplateResponse(
        "documents/list.html", _ctx(request, user, documents=docs)
    )


@router.get("/products", response_class=HTMLResponse)
async def products_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from app.models.product import Product
    from app.models.tax_rate import TaxRate

    products = list(
        await db.scalars(
            select(Product).where(Product.tenant_id == user.tenant_id).order_by(Product.name)
        )
    )
    tax_rates = list(
        await db.scalars(select(TaxRate).where(TaxRate.tenant_id == user.tenant_id))
    )
    return templates.TemplateResponse(
        "products/list.html",
        _ctx(request, user, products=products, tax_rates=tax_rates,
             tax_rates_by_id={t.id: t for t in tax_rates}),
    )


@router.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from app.models.product import Product
    from app.models.subscription import Subscription

    subscriptions = list(
        await db.scalars(
            select(Subscription)
            .where(Subscription.tenant_id == user.tenant_id)
            .order_by(Subscription.next_billing_date.asc())
        )
    )
    clients = {c.id: c for c in await db.scalars(select(Client).where(Client.tenant_id == user.tenant_id))}
    products = list(
        await db.scalars(
            select(Product).where(Product.tenant_id == user.tenant_id).order_by(Product.name)
        )
    )
    return templates.TemplateResponse(
        "subscriptions/list.html",
        _ctx(request, user, subscriptions=subscriptions, clients=clients,
             client_list=list(clients.values()), products=products),
    )


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from datetime import date as _date

    return templates.TemplateResponse(
        "reports/index.html", _ctx(request, user, current_year=_date.today().year)
    )


@router.get("/billing", response_class=HTMLResponse)
async def billing_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from app.models.tenant import Tenant
    from app.services.plans import PLANS, effective_limits, get_plan, is_in_trial, usage

    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    use = await usage(db, user.tenant_id)
    max_clients, max_invoices = effective_limits(tenant)
    return templates.TemplateResponse(
        "billing/index.html",
        _ctx(
            request, user, tenant=tenant, plans=list(PLANS.values()),
            current_plan=get_plan(tenant), in_trial=is_in_trial(tenant), usage=use,
            max_clients=max_clients, max_invoices=max_invoices,
        ),
    )


@router.get("/cabinet/collaborateurs", response_class=HTMLResponse)
async def collaborators_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from app.models.dossier import DossierAccess
    from app.models.tenant import Tenant

    tid = user.tenant_id
    access_ids = set(await db.scalars(
        select(DossierAccess.user_id).where(DossierAccess.tenant_id == tid)
    ))
    home_ids = set(await db.scalars(select(User.id).where(User.tenant_id == tid)))
    all_ids = access_ids | home_ids
    members = list(await db.scalars(
        select(User).where(User.id.in_(all_ids or {0})).order_by(User.full_name)
    ))
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tid))
    return templates.TemplateResponse(
        "cabinet/collaborators.html",
        _ctx(request, user, members=members, tenant=tenant, current_user_id=user.id),
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse(url="/login")
    from app.models.tenant import Tenant

    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    bank_accounts, _ = await _bank_context(db, user.tenant_id)
    return templates.TemplateResponse(
        "settings/index.html", _ctx(request, user, tenant=tenant, bank_accounts=bank_accounts)
    )
