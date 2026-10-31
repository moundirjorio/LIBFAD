"""Service comptable : plan de comptes CGNC, écritures, balance."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ledger import Journal, JournalEntry, JournalEntryLine, LedgerAccount

CENT = Decimal("0.01")

# Journaux par défaut.
DEFAULT_JOURNALS = [
    ("AC", "Journal des achats", "achats"),
    ("VE", "Journal des ventes", "ventes"),
    ("BQ", "Journal de banque", "banque"),
    ("CA", "Journal de caisse", "caisse"),
    ("OD", "Opérations diverses", "od"),
]

# Plan comptable CGNC (Maroc) — jeu de départ courant. (code, libellé, classe, nature)
DEFAULT_ACCOUNTS = [
    ("1111", "Capital social", 1, "passif"),
    ("1481", "Emprunts auprès des établissements de crédit", 1, "passif"),
    ("2340", "Matériel de transport", 2, "actif"),
    ("2355", "Matériel informatique", 2, "actif"),
    ("3111", "Marchandises", 3, "actif"),
    ("3421", "Clients", 3, "actif"),
    ("34552", "État - TVA récupérable sur charges", 3, "actif"),
    ("4411", "Fournisseurs", 4, "passif"),
    ("4432", "Rémunérations dues au personnel", 4, "passif"),
    ("4441", "Caisse nationale de sécurité sociale (CNSS)", 4, "passif"),
    ("4455", "État - TVA facturée", 4, "passif"),
    ("4456", "État - TVA due", 4, "passif"),
    ("5141", "Banque", 5, "tresorerie"),
    ("5161", "Caisse", 5, "tresorerie"),
    ("6111", "Achats de marchandises", 6, "charge"),
    ("61251", "Achats de fournitures non stockables", 6, "charge"),
    ("61425", "Transports et déplacements", 6, "charge"),
    ("61451", "Frais postaux et de télécommunications", 6, "charge"),
    ("61361", "Honoraires", 6, "charge"),
    ("6167", "Impôts, taxes et droits assimilés", 6, "charge"),
    ("6331", "Rémunérations du personnel", 6, "charge"),
    ("7111", "Ventes de marchandises", 7, "produit"),
    ("7124", "Ventes de services produits", 7, "produit"),
]


async def seed_ledger(db: AsyncSession, tenant_id: int) -> dict:
    """Crée le plan de comptes et les journaux par défaut si absents (idempotent)."""
    existing_accounts = {
        c for c in await db.scalars(
            select(LedgerAccount.code).where(LedgerAccount.tenant_id == tenant_id)
        )
    }
    added_acc = 0
    for code, label, cls, nature in DEFAULT_ACCOUNTS:
        if code not in existing_accounts:
            db.add(LedgerAccount(tenant_id=tenant_id, code=code, label=label,
                                 account_class=cls, nature=nature))
            added_acc += 1

    existing_journals = {
        j for j in await db.scalars(select(Journal.code).where(Journal.tenant_id == tenant_id))
    }
    added_j = 0
    for code, label, jtype in DEFAULT_JOURNALS:
        if code not in existing_journals:
            db.add(Journal(tenant_id=tenant_id, code=code, label=label, type=jtype))
            added_j += 1

    await db.commit()
    return {"accounts_added": added_acc, "journals_added": added_j}


async def create_entry(
    db: AsyncSession,
    tenant_id: int,
    journal_id: int,
    entry_date: date,
    reference: str | None,
    label: str | None,
    lines: list[dict],
) -> JournalEntry:
    """Crée une écriture équilibrée. `lines` = [{account_id, label, debit, credit}, ...]."""
    clean = []
    total_d = Decimal(0)
    total_c = Decimal(0)
    for ln in lines:
        d = Decimal(str(ln.get("debit") or 0)).quantize(CENT)
        c = Decimal(str(ln.get("credit") or 0)).quantize(CENT)
        if d == 0 and c == 0:
            continue
        if d < 0 or c < 0:
            raise HTTPException(400, "Les montants doivent être positifs.")
        if d > 0 and c > 0:
            raise HTTPException(400, "Une ligne ne peut pas avoir à la fois un débit et un crédit.")
        if not ln.get("account_id"):
            raise HTTPException(400, "Chaque ligne doit avoir un compte.")
        clean.append({"account_id": int(ln["account_id"]), "label": ln.get("label"), "debit": d, "credit": c})
        total_d += d
        total_c += c

    if len(clean) < 2:
        raise HTTPException(400, "Une écriture doit comporter au moins deux lignes.")
    if total_d != total_c:
        raise HTTPException(
            400, f"Écriture déséquilibrée : débit {total_d} ≠ crédit {total_c}."
        )

    entry = JournalEntry(
        tenant_id=tenant_id, journal_id=journal_id, entry_date=entry_date,
        reference=reference, label=label, total_debit=total_d, total_credit=total_c,
    )
    db.add(entry)
    await db.flush()
    for i, ln in enumerate(clean):
        db.add(JournalEntryLine(
            tenant_id=tenant_id, entry_id=entry.id, account_id=ln["account_id"],
            position=i, label=ln["label"], debit=ln["debit"], credit=ln["credit"],
        ))
    await db.commit()
    return entry


async def _account_id(db: AsyncSession, tenant_id: int, code: str, label: str | None = None) -> int:
    """Renvoie l'id du compte ; le crée s'il n'existe pas dans le plan."""
    acc = await db.scalar(
        select(LedgerAccount).where(LedgerAccount.tenant_id == tenant_id, LedgerAccount.code == code)
    )
    if acc:
        return acc.id
    cls = int(code[0]) if code[:1].isdigit() else 0
    acc = LedgerAccount(tenant_id=tenant_id, code=code, label=label or f"Compte {code}",
                        account_class=cls, nature="")
    db.add(acc)
    await db.flush()
    return acc.id


async def _journal_id(db: AsyncSession, tenant_id: int, code: str) -> int:
    j = await db.scalar(select(Journal).where(Journal.tenant_id == tenant_id, Journal.code == code))
    if not j:
        await seed_ledger(db, tenant_id)
        j = await db.scalar(select(Journal).where(Journal.tenant_id == tenant_id, Journal.code == code))
    return j.id


async def post_invoice_entry(db: AsyncSession, tenant_id: int, invoice) -> JournalEntry:
    """Génère l'écriture de vente d'une facture (idempotent). Journal VE.

    Débit 3421 Clients (TTC) / Crédit 7124 Ventes (HT) + Crédit 4455 TVA facturée.
    """
    if getattr(invoice, "journal_entry_id", None):
        existing = await db.scalar(select(JournalEntry).where(JournalEntry.id == invoice.journal_entry_id))
        if existing:
            return existing
    await seed_ledger(db, tenant_id)
    ttc = Decimal(str(invoice.total_ttc or 0)).quantize(CENT)
    tva = Decimal(str(invoice.total_tax or 0)).quantize(CENT)
    ht = (ttc - tva).quantize(CENT)
    lines = [
        {"account_id": await _account_id(db, tenant_id, "3421", "Clients"), "label": f"Facture {invoice.number}", "debit": ttc, "credit": 0},
        {"account_id": await _account_id(db, tenant_id, "7124", "Ventes de services produits"), "label": "Vente", "debit": 0, "credit": ht},
        {"account_id": await _account_id(db, tenant_id, "4455", "État - TVA facturée"), "label": "TVA collectée", "debit": 0, "credit": tva},
    ]
    entry = await create_entry(
        db, tenant_id, await _journal_id(db, tenant_id, "VE"),
        invoice.issue_date, invoice.number, f"Facture {invoice.number}", lines,
    )
    invoice.journal_entry_id = entry.id
    await db.commit()
    return entry


async def post_purchase_entry(db: AsyncSession, tenant_id: int, receipt) -> JournalEntry:
    """Génère l'écriture d'achat d'un reçu (idempotent). Journal AC.

    Débit compte de charge (HT) + Débit 34552 TVA récupérable / Crédit 4411 Fournisseurs (TTC).
    """
    if getattr(receipt, "journal_entry_id", None):
        existing = await db.scalar(select(JournalEntry).where(JournalEntry.id == receipt.journal_entry_id))
        if existing:
            return existing
    await seed_ledger(db, tenant_id)
    ttc = Decimal(str(receipt.amount_ttc or 0)).quantize(CENT)
    tva = Decimal(str(receipt.amount_tva or 0)).quantize(CENT)
    ht = (ttc - tva).quantize(CENT)
    charge_code = (receipt.accounting_account or "").strip() or "61251"
    lines = [
        {"account_id": await _account_id(db, tenant_id, charge_code, receipt.label), "label": receipt.label, "debit": ht, "credit": 0},
        {"account_id": await _account_id(db, tenant_id, "34552", "État - TVA récupérable sur charges"), "label": "TVA déductible", "debit": tva, "credit": 0},
        {"account_id": await _account_id(db, tenant_id, "4411", "Fournisseurs"), "label": receipt.label, "debit": 0, "credit": ttc},
    ]
    entry = await create_entry(
        db, tenant_id, await _journal_id(db, tenant_id, "AC"),
        receipt.receipt_date or receipt.deposit_date, None, f"Achat {receipt.label}", lines,
    )
    receipt.journal_entry_id = entry.id
    await db.commit()
    return entry


async def post_bank_transaction(db: AsyncSession, tenant_id: int, tx, counterpart_code: str) -> JournalEntry:
    """Comptabilise un mouvement bancaire (Journal BQ), contre le compte `counterpart_code`.

    Encaissement (montant > 0) : Débit 5141 Banque / Crédit contrepartie.
    Décaissement (montant < 0) : Débit contrepartie / Crédit 5141 Banque.
    """
    if getattr(tx, "journal_entry_id", None):
        existing = await db.scalar(select(JournalEntry).where(JournalEntry.id == tx.journal_entry_id))
        if existing:
            return existing
    await seed_ledger(db, tenant_id)
    amt = Decimal(str(tx.amount or 0)).quantize(CENT)
    if amt == 0:
        from fastapi import HTTPException as _HE
        raise _HE(400, "Montant nul.")
    bank = await _account_id(db, tenant_id, "5141", "Banque")
    other = await _account_id(db, tenant_id, counterpart_code.strip(), None)
    if amt > 0:
        lines = [
            {"account_id": bank, "label": tx.label, "debit": amt, "credit": 0},
            {"account_id": other, "label": tx.label, "debit": 0, "credit": amt},
        ]
    else:
        lines = [
            {"account_id": other, "label": tx.label, "debit": -amt, "credit": 0},
            {"account_id": bank, "label": tx.label, "debit": 0, "credit": -amt},
        ]
    entry = await create_entry(
        db, tenant_id, await _journal_id(db, tenant_id, "BQ"),
        tx.op_date, None, tx.label, lines,
    )
    tx.journal_entry_id = entry.id
    tx.reconciled = True
    await db.commit()
    return entry


def _period_bounds(year: int, month: int | None):
    from calendar import monthrange

    if month:
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])
    else:
        start = date(year, 1, 1)
        end = date(year, 12, 31)
    return start, end


async def _sum_by_prefix(db, tenant_id, prefix, start, end):
    """(débit, crédit) cumulés sur les comptes commençant par `prefix`, période donnée."""
    stmt = (
        select(
            func.coalesce(func.sum(JournalEntryLine.debit), 0),
            func.coalesce(func.sum(JournalEntryLine.credit), 0),
        )
        .join(JournalEntry, JournalEntryLine.entry_id == JournalEntry.id)
        .join(LedgerAccount, JournalEntryLine.account_id == LedgerAccount.id)
        .where(
            JournalEntryLine.tenant_id == tenant_id,
            LedgerAccount.code.like(f"{prefix}%"),
            JournalEntry.entry_date >= start, JournalEntry.entry_date <= end,
        )
    )
    d, c = (await db.execute(stmt)).one()
    return Decimal(str(d)), Decimal(str(c))


async def vat_return(db: AsyncSession, tenant_id: int, year: int, month: int | None) -> dict:
    """Déclaration de TVA : collectée (4455), déductible (3455), TVA due."""
    start, end = _period_bounds(year, month)
    cd, cc = await _sum_by_prefix(db, tenant_id, "4455", start, end)   # TVA facturée
    dd, dc = await _sum_by_prefix(db, tenant_id, "3455", start, end)   # TVA récupérable
    collectee = (cc - cd).quantize(CENT)     # crédit net
    deductible = (dd - dc).quantize(CENT)     # débit net
    due = (collectee - deductible).quantize(CENT)
    return {
        "year": year, "month": month, "start": start, "end": end,
        "collectee": float(collectee), "deductible": float(deductible),
        "due": float(max(due, Decimal(0))),
        "credit_reportable": float(max(-due, Decimal(0))),
        "net": float(due),
    }


async def post_vat_entry(db: AsyncSession, tenant_id: int, year: int, month: int | None) -> JournalEntry:
    """Écriture de liquidation de la TVA de la période (journal OD).

    Débit 4455 (collectée) / Crédit 3455 (déductible) / solde vers 4456 TVA due.
    """
    vr = await vat_return(db, tenant_id, year, month)
    collectee = Decimal(str(vr["collectee"]))
    deductible = Decimal(str(vr["deductible"]))
    net = Decimal(str(vr["net"]))
    if collectee == 0 and deductible == 0:
        from fastapi import HTTPException as _HE
        raise _HE(400, "Aucune TVA sur la période.")
    lines = [
        {"account_id": await _account_id(db, tenant_id, "4455", "État - TVA facturée"), "label": "TVA collectée", "debit": collectee, "credit": 0},
        {"account_id": await _account_id(db, tenant_id, "34552", "État - TVA récupérable sur charges"), "label": "TVA déductible", "debit": 0, "credit": deductible},
    ]
    if net > 0:
        lines.append({"account_id": await _account_id(db, tenant_id, "4456", "État - TVA due"), "label": "TVA due", "debit": 0, "credit": net})
    elif net < 0:
        lines.append({"account_id": await _account_id(db, tenant_id, "3456", "État - crédit de TVA"), "label": "Crédit de TVA", "debit": -net, "credit": 0})
    label = f"Déclaration TVA {vr['start']:%m/%Y}" if month else f"Déclaration TVA {year}"
    return await create_entry(db, tenant_id, await _journal_id(db, tenant_id, "OD"), vr["end"], None, label, lines)


async def financial_statements(db: AsyncSession, tenant_id: int) -> dict:
    """États de synthèse simplifiés (CGNC) : CPC et Bilan, calculés depuis la balance."""
    tb = await trial_balance(db, tenant_id)

    charges: list[dict] = []
    produits: list[dict] = []
    groups = {
        "immobilise": [], "circulant_actif": [], "tresorerie_actif": [],
        "financement": [], "circulant_passif": [], "tresorerie_passif": [],
    }
    tot_charges = tot_produits = 0.0
    for r in tb["rows"]:
        code = r["code"]
        cls = int(code[0]) if code[:1].isdigit() else 0
        sd, sc = r["solde_debit"], r["solde_credit"]
        if cls == 6:
            charges.append({"code": code, "label": r["label"], "amount": sd - sc}); tot_charges += sd - sc
        elif cls == 7:
            produits.append({"code": code, "label": r["label"], "amount": sc - sd}); tot_produits += sc - sd
        elif cls in (1, 2, 3, 4, 5):
            # Compte de bilan : à l'ACTIF si solde débiteur, au PASSIF si créditeur
            # (quelle que soit la classe — gère clients créditeurs, fournisseurs débiteurs…).
            if sd > 0:
                key = "immobilise" if cls == 2 else ("tresorerie_actif" if cls == 5 else "circulant_actif")
                groups[key].append({"code": code, "label": r["label"], "amount": sd})
            elif sc > 0:
                key = "financement" if cls == 1 else ("tresorerie_passif" if cls == 5 else "circulant_passif")
                groups[key].append({"code": code, "label": r["label"], "amount": sc})

    resultat = round(tot_produits - tot_charges, 2)

    def _sum(key):
        return round(sum(i["amount"] for i in groups[key]), 2)

    total_actif = round(_sum("immobilise") + _sum("circulant_actif") + _sum("tresorerie_actif"), 2)
    total_passif_hors_res = round(_sum("financement") + _sum("circulant_passif") + _sum("tresorerie_passif"), 2)
    total_passif = round(total_passif_hors_res + resultat, 2)

    return {
        "cpc": {
            "produits": produits, "charges": charges,
            "total_produits": round(tot_produits, 2), "total_charges": round(tot_charges, 2),
            "resultat": resultat,
        },
        "bilan": {
            "groups": groups,
            "sum": {k: _sum(k) for k in groups},
            "resultat": resultat,
            "total_actif": total_actif,
            "total_passif": total_passif,
            "balanced": abs(total_actif - total_passif) < 0.01,
        },
    }


async def general_ledger(db: AsyncSession, tenant_id: int, account_id: int) -> dict:
    """Grand livre d'un compte : mouvements chronologiques + solde progressif."""
    account = await db.scalar(
        select(LedgerAccount).where(LedgerAccount.id == account_id, LedgerAccount.tenant_id == tenant_id)
    )
    if not account:
        return {"account": None, "rows": []}
    stmt = (
        select(
            JournalEntry.entry_date, JournalEntry.reference, Journal.code.label("jcode"),
            JournalEntryLine.label, JournalEntryLine.debit, JournalEntryLine.credit,
        )
        .join(JournalEntry, JournalEntryLine.entry_id == JournalEntry.id)
        .join(Journal, JournalEntry.journal_id == Journal.id)
        .where(JournalEntryLine.account_id == account_id)
        .order_by(JournalEntry.entry_date, JournalEntry.id, JournalEntryLine.position)
    )
    rows = (await db.execute(stmt)).all()
    out = []
    solde = Decimal(0)
    td = tc = Decimal(0)
    for r in rows:
        d = Decimal(str(r.debit)); c = Decimal(str(r.credit))
        solde += d - c
        td += d; tc += c
        out.append({
            "date": r.entry_date, "journal": r.jcode, "reference": r.reference,
            "label": r.label, "debit": float(d), "credit": float(c), "solde": float(solde),
        })
    return {
        "account": {"code": account.code, "label": account.label},
        "rows": out, "total_debit": float(td), "total_credit": float(tc), "solde": float(solde),
    }


async def fec_rows(db: AsyncSession, tenant_id: int) -> list[list[str]]:
    """Lignes du FEC (Fichier des Écritures Comptables) — format tabulé standard."""
    stmt = (
        select(
            Journal.code, Journal.label,
            JournalEntry.id, JournalEntry.entry_date, JournalEntry.reference, JournalEntry.label,
            LedgerAccount.code, LedgerAccount.label,
            JournalEntryLine.label, JournalEntryLine.debit, JournalEntryLine.credit,
        )
        .join(JournalEntry, JournalEntryLine.entry_id == JournalEntry.id)
        .join(Journal, JournalEntry.journal_id == Journal.id)
        .join(LedgerAccount, JournalEntryLine.account_id == LedgerAccount.id)
        .where(JournalEntryLine.tenant_id == tenant_id)
        .order_by(JournalEntry.entry_date, JournalEntry.id, JournalEntryLine.position)
    )
    rows = (await db.execute(stmt)).all()

    def d(x):
        return f"{Decimal(str(x)):.2f}".replace(".", ",")

    out = []
    for r in rows:
        ds = r.entry_date.strftime("%Y%m%d")
        out.append([
            r[0],                      # JournalCode
            r[1],                      # JournalLib
            f"{r[2]:06d}",             # EcritureNum
            ds,                        # EcritureDate
            r[6],                      # CompteNum
            r[7],                      # CompteLib
            "", "",                    # CompAuxNum, CompAuxLib
            r[4] or f"{r[2]:06d}",     # PieceRef
            ds,                        # PieceDate
            (r[8] or r[5] or ""),      # EcritureLib
            d(r[9]),                   # Debit
            d(r[10]),                  # Credit
            "", "",                    # EcritureLet, DateLet
            ds,                        # ValidDate
            "", "",                    # Montantdevise, Idevise
        ])
    return out


FEC_HEADER = [
    "JournalCode", "JournalLib", "EcritureNum", "EcritureDate", "CompteNum", "CompteLib",
    "CompAuxNum", "CompAuxLib", "PieceRef", "PieceDate", "EcritureLib", "Debit", "Credit",
    "EcritureLet", "DateLet", "ValidDate", "Montantdevise", "Idevise",
]


async def trial_balance(db: AsyncSession, tenant_id: int) -> dict:
    """Balance générale : par compte, total débit/crédit et solde."""
    stmt = (
        select(
            LedgerAccount.id, LedgerAccount.code, LedgerAccount.label,
            func.coalesce(func.sum(JournalEntryLine.debit), 0).label("debit"),
            func.coalesce(func.sum(JournalEntryLine.credit), 0).label("credit"),
        )
        .join(JournalEntryLine, JournalEntryLine.account_id == LedgerAccount.id)
        .where(LedgerAccount.tenant_id == tenant_id)
        .group_by(LedgerAccount.id, LedgerAccount.code, LedgerAccount.label)
        .order_by(LedgerAccount.code)
    )
    rows = (await db.execute(stmt)).all()
    out = []
    td = tc = tsd = tsc = Decimal(0)
    for r in rows:
        d = Decimal(str(r.debit))
        c = Decimal(str(r.credit))
        solde = d - c
        sd = solde if solde > 0 else Decimal(0)
        sc = -solde if solde < 0 else Decimal(0)
        out.append({"code": r.code, "label": r.label, "debit": float(d), "credit": float(c),
                    "solde_debit": float(sd), "solde_credit": float(sc)})
        td += d; tc += c; tsd += sd; tsc += sc
    return {
        "rows": out,
        "total_debit": float(td), "total_credit": float(tc),
        "total_solde_debit": float(tsd), "total_solde_credit": float(tsc),
        "balanced": td == tc,
    }
