"""Actions métier exposées aux assistants conversationnels (bot Telegram, IA).

Chaque action reçoit la session et l'utilisateur LIBFAD (donc son tenant),
applique les mêmes règles que l'interface et renvoie un dict sérialisable.
Une erreur « normale » (client introuvable, montant invalide…) lève
`ActionError` avec un message affichable tel quel à l'utilisateur.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.bank import BankAccount, BankTransaction
from app.models.client import Client
from app.models.invoice import Invoice
from app.models.purchase_receipt import PurchaseReceipt
from app.models.quote import Quote
from app.models.supplier import Supplier
from app.models.tenant import Tenant
from app.models.user import User

UNPAID_STATUSES = ("sent", "issued", "overdue", "partially_paid")

STATUS_FR = {
    "draft": "Brouillon", "issued": "Émise", "sent": "Envoyée", "partially_paid": "Partiel",
    "paid": "Payée", "overdue": "En retard", "cancelled": "Annulée", "refunded": "Remboursée",
    "accepted": "Accepté", "refused": "Refusé", "expired": "Expiré", "converted": "Converti",
}


class ActionError(Exception):
    """Erreur métier attendue, dont le message est destiné à l'utilisateur."""


def to_decimal(value, field: str = "montant") -> Decimal:
    """Accepte 1200, "1 200,50", "1200.5"… et renvoie un Decimal."""
    if isinstance(value, Decimal):
        return value
    s = str(value if value is not None else "").strip().replace(" ", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        raise ActionError(f"Valeur invalide pour {field} : « {value} ».") from None


async def get_tenant(db: AsyncSession, user: User) -> Tenant:
    return await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))


# --- Recherche d'objets par référence « humaine » -----------------------------

async def find_client(db: AsyncSession, tenant_id: int, ref) -> Client:
    ref = str(ref or "").strip()
    if not ref:
        raise ActionError("Précisez le client.")
    if ref.isdigit():
        c = await db.scalar(select(Client).where(Client.tenant_id == tenant_id, Client.id == int(ref)))
        if c:
            return c
    rows = list(await db.scalars(
        select(Client).where(Client.tenant_id == tenant_id, Client.name.ilike(f"%{ref}%")).order_by(Client.name)
    ))
    exact = [c for c in rows if c.name.lower() == ref.lower()]
    if len(exact) == 1:
        return exact[0]
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise ActionError(f"Aucun client ne correspond à « {ref} ».")
    names = ", ".join(c.name for c in rows[:6])
    raise ActionError(f"Plusieurs clients correspondent à « {ref} » : {names}. Précisez.")


async def _find_document(db: AsyncSession, model, tenant_id: int, ref, label: str, with_items: bool):
    ref = str(ref or "").strip().lstrip("#")
    if not ref:
        raise ActionError(f"Précisez le numéro {label}.")
    base = select(model).where(model.tenant_id == tenant_id)
    if with_items:
        base = base.options(selectinload(model.items))
    doc = await db.scalar(base.where(func.lower(model.number) == ref.lower()))
    if doc:
        return doc
    rows = list(await db.scalars(base.where(model.number.ilike(f"%{ref}")).order_by(model.id.desc())))
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise ActionError(f"Aucun numéro {label} ne correspond à « {ref} ».")
    nums = ", ".join(d.number for d in rows[:6])
    raise ActionError(f"Plusieurs numéros {label} se terminent par « {ref} » : {nums}. Précisez.")


async def find_invoice(db: AsyncSession, tenant_id: int, ref, *, with_items: bool = False) -> Invoice:
    return await _find_document(db, Invoice, tenant_id, ref, "de facture", with_items)


async def find_quote(db: AsyncSession, tenant_id: int, ref, *, with_items: bool = False) -> Quote:
    return await _find_document(db, Quote, tenant_id, ref, "de devis", with_items)


def _is_unpaid(inv: Invoice) -> bool:
    return inv.status in UNPAID_STATUSES and float(inv.amount_due or 0) > 0


def _invoice_row(inv: Invoice, client_name: str | None) -> dict:
    unpaid = _is_unpaid(inv)
    return {
        "numero": inv.number,
        "client": client_name or "—",
        "statut": STATUS_FR.get(inv.status, inv.status),
        "date": inv.issue_date.isoformat(),
        "echeance": inv.due_date.isoformat(),
        "total_ttc": float(inv.total_ttc or 0),
        "reste_du": float(inv.amount_due or 0) if inv.status not in ("paid", "cancelled") else 0.0,
        "devise": inv.currency,
        "en_retard": bool(unpaid and inv.due_date < date.today()),
    }


async def _client_names(db: AsyncSession, tenant_id: int) -> dict[int, str]:
    rows = await db.execute(select(Client.id, Client.name).where(Client.tenant_id == tenant_id))
    return {cid: name for cid, name in rows.all()}


# --- Lecture ------------------------------------------------------------------

async def summary(db: AsyncSession, user: User) -> dict:
    """Indicateurs clés. Les montants sont ventilés par devise (pas de somme mixte)."""
    tid = user.tenant_id
    tenant = await get_tenant(db, user)
    today = date.today()

    async def by_currency(column, *conds) -> dict[str, float]:
        rows = await db.execute(
            select(Invoice.currency, func.coalesce(func.sum(column), 0))
            .where(Invoice.tenant_id == tid, *conds).group_by(Invoice.currency)
        )
        return {cur: float(total) for cur, total in rows.all() if float(total)}

    live = Invoice.status.notin_(("draft", "cancelled"))
    unpaid = (Invoice.status.in_(UNPAID_STATUSES), Invoice.amount_due > 0)
    month_start = today.replace(day=1)
    return {
        "societe": tenant.name,
        "devise_societe": tenant.currency,
        "clients": await db.scalar(select(func.count(Client.id)).where(Client.tenant_id == tid)) or 0,
        "facture": await by_currency(Invoice.total_ttc, live),
        "encaisse": await by_currency(Invoice.amount_paid, live),
        "a_encaisser": await by_currency(Invoice.amount_due, *unpaid),
        "nb_impayees": await db.scalar(
            select(func.count(Invoice.id)).where(Invoice.tenant_id == tid, *unpaid)) or 0,
        "en_retard": await by_currency(Invoice.amount_due, *unpaid, Invoice.due_date < today),
        "nb_en_retard": await db.scalar(
            select(func.count(Invoice.id)).where(Invoice.tenant_id == tid, *unpaid, Invoice.due_date < today)) or 0,
        "devis_en_cours": await db.scalar(
            select(func.count(Quote.id)).where(Quote.tenant_id == tid, Quote.status.in_(("draft", "sent")))) or 0,
        "tresorerie": float(await db.scalar(
            select(func.coalesce(func.sum(BankAccount.current_balance), 0)).where(BankAccount.tenant_id == tid)) or 0),
        "achats_du_mois": float(await db.scalar(
            select(func.coalesce(func.sum(PurchaseReceipt.amount_ttc), 0)).where(
                PurchaseReceipt.tenant_id == tid, PurchaseReceipt.deposit_date >= month_start)) or 0),
    }


async def list_invoices(db: AsyncSession, user: User, statut: str | None = None,
                        client: str | None = None, limit: int = 10) -> dict:
    """statut : impayees | en_retard | payees | brouillons | toutes."""
    tid = user.tenant_id
    stmt = select(Invoice).where(Invoice.tenant_id == tid)
    statut = (statut or "toutes").lower()
    if statut == "impayees":
        stmt = stmt.where(Invoice.status.in_(UNPAID_STATUSES), Invoice.amount_due > 0)
    elif statut == "en_retard":
        stmt = stmt.where(Invoice.status.in_(UNPAID_STATUSES), Invoice.amount_due > 0,
                          Invoice.due_date < date.today())
    elif statut == "payees":
        stmt = stmt.where(Invoice.status == "paid")
    elif statut == "brouillons":
        stmt = stmt.where(Invoice.status == "draft")
    if client:
        stmt = stmt.where(Invoice.client_id == (await find_client(db, tid, client)).id)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(await db.scalars(
        stmt.order_by(Invoice.issue_date.desc(), Invoice.id.desc()).limit(max(1, min(int(limit or 10), 30)))
    ))
    names = await _client_names(db, tid)
    return {"total": total, "factures": [_invoice_row(i, names.get(i.client_id)) for i in rows]}


async def invoice_detail(db: AsyncSession, user: User, numero: str) -> dict:
    inv = await find_invoice(db, user.tenant_id, numero, with_items=True)
    client = await db.scalar(select(Client).where(Client.id == inv.client_id))
    row = _invoice_row(inv, client.name if client else None)
    row.update({
        "total_ht": float(inv.subtotal_ht or 0),
        "tva": float(inv.total_tax or 0),
        "deja_paye": float(inv.amount_paid or 0),
        "lignes": [
            {"description": it.description, "quantite": float(it.quantity), "prix_unitaire_ht": float(it.unit_price),
             "tva_pct": float(it.tax_rate), "total_ht": float(it.line_subtotal_ht)}
            for it in inv.items
        ],
    })
    return row


async def unpaid_by_client(db: AsyncSession, user: User) -> dict:
    tid = user.tenant_id
    rows = list(await db.scalars(
        select(Invoice).where(Invoice.tenant_id == tid, Invoice.status.in_(UNPAID_STATUSES),
                              Invoice.amount_due > 0).order_by(Invoice.due_date)
    ))
    names = await _client_names(db, tid)
    groups: dict[tuple[int, str], dict] = {}
    for inv in rows:
        g = groups.setdefault((inv.client_id, inv.currency), {
            "client": names.get(inv.client_id, "—"), "devise": inv.currency, "total_du": 0.0, "factures": []})
        g["total_du"] += float(inv.amount_due)
        g["factures"].append(_invoice_row(inv, g["client"]))
    return {"clients": sorted(groups.values(), key=lambda g: -g["total_du"])}


async def list_clients(db: AsyncSession, user: User, recherche: str | None = None, limit: int = 15) -> dict:
    stmt = select(Client).where(Client.tenant_id == user.tenant_id)
    if recherche:
        stmt = stmt.where(Client.name.ilike(f"%{recherche.strip()}%"))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(await db.scalars(stmt.order_by(Client.name).limit(max(1, min(int(limit or 15), 40)))))
    return {"total": total, "clients": [
        {"id": c.id, "nom": c.name, "email": c.email, "telephone": c.phone, "ville": c.billing_city}
        for c in rows
    ]}


async def list_quotes(db: AsyncSession, user: User, limit: int = 10) -> dict:
    tid = user.tenant_id
    rows = list(await db.scalars(
        select(Quote).where(Quote.tenant_id == tid).order_by(Quote.issue_date.desc(), Quote.id.desc())
        .limit(max(1, min(int(limit or 10), 30)))
    ))
    names = await _client_names(db, tid)
    return {"devis": [
        {"numero": q.number, "client": names.get(q.client_id, "—"), "statut": STATUS_FR.get(q.status, q.status),
         "date": q.issue_date.isoformat(), "valable_jusqu_au": q.valid_until.isoformat(),
         "total_ttc": float(q.total_ttc or 0), "devise": q.currency}
        for q in rows
    ]}


async def list_suppliers(db: AsyncSession, user: User, recherche: str | None = None) -> dict:
    stmt = select(Supplier).where(Supplier.tenant_id == user.tenant_id)
    if recherche:
        stmt = stmt.where(Supplier.name.ilike(f"%{recherche.strip()}%"))
    rows = list(await db.scalars(stmt.order_by(Supplier.name).limit(40)))
    return {"fournisseurs": [
        {"id": s.id, "nom": s.name, "email": s.email, "telephone": s.phone, "compte": s.default_account}
        for s in rows
    ]}


async def list_purchases(db: AsyncSession, user: User, limit: int = 10) -> dict:
    tid = user.tenant_id
    rows = list(await db.scalars(
        select(PurchaseReceipt).where(PurchaseReceipt.tenant_id == tid)
        .order_by(PurchaseReceipt.deposit_date.desc(), PurchaseReceipt.id.desc())
        .limit(max(1, min(int(limit or 10), 30)))
    ))
    month_start = date.today().replace(day=1)
    month_total = await db.scalar(
        select(func.coalesce(func.sum(PurchaseReceipt.amount_ttc), 0)).where(
            PurchaseReceipt.tenant_id == tid, PurchaseReceipt.deposit_date >= month_start))
    return {"total_du_mois": float(month_total or 0), "achats": [
        {"date": r.deposit_date.isoformat(), "intitule": r.label, "compte": r.accounting_account,
         "montant_ttc": float(r.amount_ttc or 0), "tva": float(r.amount_tva or 0),
         "statut": "Validé" if r.status == "validated" else "À vérifier"}
        for r in rows
    ]}


async def treasury(db: AsyncSession, user: User) -> dict:
    tid = user.tenant_id
    accounts = list(await db.scalars(
        select(BankAccount).where(BankAccount.tenant_id == tid).order_by(BankAccount.name)))
    last = list(await db.scalars(
        select(BankTransaction).where(BankTransaction.tenant_id == tid)
        .order_by(BankTransaction.op_date.desc(), BankTransaction.id.desc()).limit(6)))
    pending = await db.scalar(
        select(func.count(BankTransaction.id)).where(
            BankTransaction.tenant_id == tid, BankTransaction.reconciled.is_(False), BankTransaction.amount > 0))
    return {
        "comptes": [{"nom": a.name, "banque": a.bank_name, "solde": float(a.current_balance or 0),
                     "devise": a.currency} for a in accounts],
        "derniers_mouvements": [{"date": t.op_date.isoformat(), "libelle": t.label, "montant": float(t.amount)}
                                for t in last],
        "encaissements_a_rapprocher": pending or 0,
    }


# --- Écriture -----------------------------------------------------------------

async def create_client(db: AsyncSession, user: User, nom: str, email: str | None = None,
                        telephone: str | None = None, ice: str | None = None,
                        ville: str | None = None, pays: str | None = None) -> dict:
    nom = (nom or "").strip()
    if not nom:
        raise ActionError("Le nom du client est obligatoire.")
    exists = await db.scalar(
        select(Client).where(Client.tenant_id == user.tenant_id, func.lower(Client.name) == nom.lower()))
    if exists:
        raise ActionError(f"Le client « {exists.name} » existe déjà.")
    tenant = await get_tenant(db, user)
    client = Client(
        tenant_id=user.tenant_id, name=nom[:200], email=(email or None), phone=(telephone or None),
        ice=(ice or None), billing_city=(ville or None),
        billing_country=(pays or tenant.country or "MA")[:2].upper(), currency=tenant.currency,
    )
    db.add(client)
    await db.commit()
    return {"id": client.id, "nom": client.name, "email": client.email, "telephone": client.phone}


async def create_supplier(db: AsyncSession, user: User, nom: str, email: str | None = None,
                          telephone: str | None = None, ice: str | None = None,
                          compte: str | None = None) -> dict:
    nom = (nom or "").strip()
    if not nom:
        raise ActionError("Le nom du fournisseur est obligatoire.")
    exists = await db.scalar(
        select(Supplier).where(Supplier.tenant_id == user.tenant_id, func.lower(Supplier.name) == nom.lower()))
    if exists:
        raise ActionError(f"Le fournisseur « {exists.name} » existe déjà.")
    sup = Supplier(tenant_id=user.tenant_id, name=nom[:200], email=(email or None),
                   phone=(telephone or None), ice=(ice or None), default_account=(compte or None))
    db.add(sup)
    await db.commit()
    return {"id": sup.id, "nom": sup.name, "compte": sup.default_account}


def _clean_lines(lignes) -> list[dict]:
    if not lignes:
        raise ActionError("Ajoutez au moins une ligne (description, quantité, prix unitaire HT).")
    items = []
    for i, ln in enumerate(lignes):
        desc = str(ln.get("description") or "").strip()
        if not desc:
            raise ActionError(f"Ligne {i + 1} : description manquante.")
        qty = to_decimal(ln.get("quantite", 1), "quantité")
        price = to_decimal(ln.get("prix_unitaire_ht", 0), "prix unitaire")
        tax = to_decimal(ln.get("tva_pct", 0), "TVA")
        if qty <= 0:
            raise ActionError(f"Ligne {i + 1} : la quantité doit être positive.")
        if price < 0:
            raise ActionError(f"Ligne {i + 1} : le prix ne peut pas être négatif.")
        if not (0 <= tax <= 100):
            raise ActionError(f"Ligne {i + 1} : la TVA doit être entre 0 et 100 %.")
        items.append({"description": desc, "quantity": qty, "unit_price": price, "tax_rate": tax,
                      "discount_rate": Decimal(0), "unit": "u", "position": i})
    return items


def _locale(langue: str | None) -> str:
    return "en_US" if str(langue or "").lower().startswith("en") else "fr_FR"


async def create_invoice(db: AsyncSession, user: User, client: str, lignes: list[dict],
                         devise: str | None = None, langue: str | None = None,
                         delai_jours: int = 30, objet: str | None = None, emettre: bool = False) -> dict:
    """Crée une facture (brouillon par défaut, émise si `emettre`)."""
    from app.routers.invoices import create_invoice as _create
    from app.schemas.invoice import InvoiceIn

    cli = await find_client(db, user.tenant_id, client)
    tenant = await get_tenant(db, user)
    payload = InvoiceIn(
        client_id=cli.id, currency=(devise or cli.currency or tenant.currency or "MAD").upper()[:3],
        locale=_locale(langue), payment_terms_days=int(delai_jours or 30),
        notes_public=(objet or None), items=_clean_lines(lignes),
    )
    try:
        inv = await _create(payload, user=user, db=db)
    except HTTPException as exc:  # quota de plan, client introuvable…
        raise ActionError(str(exc.detail)) from None
    if emettre:
        inv.status = "issued"
        await db.commit()
    return _invoice_row(inv, cli.name)


async def create_quote(db: AsyncSession, user: User, client: str, lignes: list[dict],
                       devise: str | None = None, langue: str | None = None,
                       validite_jours: int = 30, objet: str | None = None) -> dict:
    from app.routers.quotes import create_quote as _create
    from app.schemas.quote import QuoteIn

    cli = await find_client(db, user.tenant_id, client)
    tenant = await get_tenant(db, user)
    today = date.today()
    payload = QuoteIn(
        client_id=cli.id, issue_date=today, valid_until=today + timedelta(days=int(validite_jours or 30)),
        currency=(devise or cli.currency or tenant.currency or "MAD").upper()[:3],
        locale=_locale(langue), notes_public=(objet or None), items=_clean_lines(lignes),
    )
    try:
        q = await _create(payload, user=user, db=db)
    except HTTPException as exc:
        raise ActionError(str(exc.detail)) from None
    return {"numero": q.number, "client": cli.name, "statut": STATUS_FR.get(q.status, q.status),
            "total_ttc": float(q.total_ttc or 0), "devise": q.currency,
            "valable_jusqu_au": q.valid_until.isoformat()}


async def issue_invoice(db: AsyncSession, user: User, numero: str) -> dict:
    inv = await find_invoice(db, user.tenant_id, numero)
    if inv.status != "draft":
        raise ActionError(f"La facture {inv.number} n'est pas un brouillon (statut : {STATUS_FR.get(inv.status, inv.status)}).")
    inv.status = "issued"
    await db.commit()
    names = await _client_names(db, user.tenant_id)
    return _invoice_row(inv, names.get(inv.client_id))


async def record_payment(db: AsyncSession, user: User, numero: str, montant=None,
                         methode: str = "transfer", reference: str | None = None) -> dict:
    """Encaisse un paiement. Sans montant : solde le reste dû."""
    from app.routers.payments import record_payment as _record
    from app.schemas.payment import PaymentIn

    inv = await find_invoice(db, user.tenant_id, numero)
    if inv.status == "cancelled":
        raise ActionError(f"La facture {inv.number} est annulée.")
    due = Decimal(str(inv.amount_due or 0))
    if due <= 0:
        raise ActionError(f"La facture {inv.number} est déjà soldée.")
    amount = due if montant in (None, "") else to_decimal(montant)
    if amount <= 0:
        raise ActionError("Le montant du paiement doit être positif.")
    if amount > due + Decimal("0.01"):
        raise ActionError(f"Le montant ({amount}) dépasse le reste dû ({due} {inv.currency}).")
    method = methode if methode in ("transfer", "card", "cash", "check") else "transfer"
    try:
        await _record(PaymentIn(invoice_id=inv.id, amount=amount, currency=inv.currency,
                                method=method, reference=reference), user=user, db=db)
    except HTTPException as exc:
        raise ActionError(str(exc.detail)) from None
    await db.refresh(inv)
    names = await _client_names(db, user.tenant_id)
    row = _invoice_row(inv, names.get(inv.client_id))
    row["paiement_enregistre"] = float(amount)
    return row


async def add_purchase(db: AsyncSession, user: User, intitule: str, montant_ttc, tva=0,
                       fournisseur: str | None = None, compte: str | None = None) -> dict:
    intitule = (intitule or "").strip()
    if not intitule:
        raise ActionError("L'intitulé de l'achat est obligatoire.")
    ttc = to_decimal(montant_ttc, "montant TTC")
    vat = to_decimal(tva or 0, "TVA")
    if ttc <= 0:
        raise ActionError("Le montant TTC doit être positif.")
    if vat < 0 or vat > ttc:
        raise ActionError("La TVA doit être comprise entre 0 et le montant TTC.")
    sup = None
    if fournisseur:
        sup = await db.scalar(select(Supplier).where(
            Supplier.tenant_id == user.tenant_id, Supplier.name.ilike(f"%{fournisseur.strip()}%")))
    tenant = await get_tenant(db, user)
    rec = PurchaseReceipt(
        tenant_id=user.tenant_id, kind="receipt", deposit_date=date.today(), receipt_date=date.today(),
        label=intitule[:200], supplier_label=(sup.name if sup else intitule)[:200],
        supplier_id=sup.id if sup else None,
        accounting_account=(compte or (sup.default_account if sup else None) or None),
        amount_ttc=ttc, amount_tva=vat, bank_account_id=tenant.default_bank_account_id,
    )
    db.add(rec)
    await db.commit()
    return {"intitule": rec.label, "montant_ttc": float(ttc), "tva": float(vat),
            "fournisseur": sup.name if sup else None, "compte": rec.accounting_account}


async def document_pdf(db: AsyncSession, user: User, type: str, numero: str) -> dict:
    """Génère le PDF d'une facture ou d'un devis et renvoie son chemin."""
    from app.services.pdf import render_pdf, storage_pdf_path

    tenant = await get_tenant(db, user)
    if str(type).lower().startswith("d"):
        doc = await find_quote(db, user.tenant_id, numero, with_items=True)
        kind, template, key = "quote", "pdf/quote.html", "quote"
    else:
        doc = await find_invoice(db, user.tenant_id, numero, with_items=True)
        kind, template, key = "invoice", "pdf/invoice.html", "invoice"
    client = await db.scalar(select(Client).where(Client.id == doc.client_id))
    out = storage_pdf_path(kind, user.tenant_id, doc.number)
    result = await render_pdf(template, {key: doc, "client": client, "tenant": tenant, "settings": settings}, out)
    if Path(result).suffix.lower() != ".pdf":
        raise ActionError("La génération du PDF a échoué.")
    doc.pdf_path = str(out)
    await db.commit()
    return {"path": str(out), "filename": f"{doc.number}.pdf", "numero": doc.number,
            "client": client.name if client else "—"}


async def reminder_message(db: AsyncSession, user: User, client: str | None = None,
                           numero: str | None = None) -> dict:
    """Prépare le message de relance (et le lien WhatsApp si le client a un téléphone)."""
    from urllib.parse import quote

    from app.routers.dashboard import _whatsapp_number

    tid = user.tenant_id
    if numero:
        inv = await find_invoice(db, tid, numero)
        cli = await db.scalar(select(Client).where(Client.id == inv.client_id))
        invoices = [inv] if _is_unpaid(inv) else []
        if not invoices:
            raise ActionError(f"La facture {inv.number} n'a pas de reste dû à relancer.")
    else:
        cli = await find_client(db, tid, client)
        invoices = [i for i in await db.scalars(
            select(Invoice).where(Invoice.tenant_id == tid, Invoice.client_id == cli.id).order_by(Invoice.due_date)
        ) if _is_unpaid(i)]
        if not invoices:
            raise ActionError(f"{cli.name} n'a aucune facture impayée.")
    signature = user.full_name or settings.app_name
    lines = [f"- Facture {i.number} : {float(i.amount_due):.2f} {i.currency} "
             f"(échéance {i.due_date.strftime('%d/%m/%Y')})" for i in invoices]
    totals: dict[str, float] = {}
    for i in invoices:
        totals[i.currency] = totals.get(i.currency, 0.0) + float(i.amount_due)
    total_txt = " + ".join(f"{v:.2f} {c}" for c, v in totals.items())
    text = ("Bonjour, voici le récapitulatif de vos factures en attente de règlement :\n"
            + "\n".join(lines) + f"\nTotal restant dû : {total_txt}.\n"
            f"Merci d'avance pour votre règlement. Cordialement, {signature}.")
    wa = _whatsapp_number(cli.phone, cli.billing_country)
    return {"client": cli.name, "message": text, "total": total_txt,
            "whatsapp_url": f"https://wa.me/{wa}?text={quote(text)}" if wa else None}


async def import_statement(db: AsyncSession, user: User, data: bytes, filename: str) -> dict:
    """Importe un relevé bancaire (PDF ou CSV) dans le compte par défaut."""
    from app.services.bank import import_csv, import_pdf

    tid = user.tenant_id
    accounts = list(await db.scalars(
        select(BankAccount).where(BankAccount.tenant_id == tid).order_by(BankAccount.name)))
    if not accounts:
        raise ActionError("Aucun compte bancaire. Créez-en un dans l'interface (Trésorerie) avant d'importer.")
    tenant = await get_tenant(db, user)
    account = next((a for a in accounts if a.id == tenant.default_bank_account_id), None)
    if account is None:
        if len(accounts) > 1:
            raise ActionError("Plusieurs comptes bancaires et aucun compte par défaut : "
                              "choisissez-en un dans Paramètres, ou importez depuis l'interface.")
        account = accounts[0]
    if data[:5] == b"%PDF-" or filename.lower().endswith(".pdf"):
        result = await import_pdf(db, tid, account, data)
    else:
        result = await import_csv(db, tid, account, data.decode("utf-8-sig", errors="replace"))
    await db.refresh(account)
    return {"compte": account.name, "importes": result.get("imported", 0),
            "ignores": result.get("skipped", 0), "solde": float(account.current_balance or 0),
            "devise": account.currency}
