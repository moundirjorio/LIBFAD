"""Service trésorerie : import de relevés, soldes, synthèse."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank import BankAccount, BankTransaction

# Colonnes reconnues (insensibles casse/espaces).
_COLS = {
    "date": "date", "date opération": "date", "date operation": "date", "date valeur": "date",
    "libellé": "label", "libelle": "label", "label": "label", "description": "label", "intitulé": "label",
    "montant": "amount", "amount": "amount",
    "débit": "debit", "debit": "debit", "crédit": "credit", "credit": "credit",
}


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y",
                "%d.%m.%Y", "%d.%m.%y", "%d-%m-%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _num(s: str) -> Decimal:
    s = (s or "").strip().replace(" ", "").replace(" ", "")
    # signe « - » en fin (relevés type « 1200,00- ») → déplacé en préfixe
    if s.endswith("-"):
        s = "-" + s[:-1]
    if not s:
        return Decimal(0)
    # gère "1 234,56" et "1,234.56"
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal(0)


async def recompute_balance(db: AsyncSession, account: BankAccount) -> None:
    total = await db.scalar(
        select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
            BankTransaction.account_id == account.id
        )
    )
    account.current_balance = Decimal(str(account.opening_balance or 0)) + Decimal(str(total or 0))


async def import_csv(db: AsyncSession, tenant_id: int, account: BankAccount, raw: str) -> dict:
    """Importe un relevé CSV. Colonnes FR/EN, séparateur , ou ;. Montant signé
    OU colonnes débit/crédit séparées."""
    sample = raw[:2000]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)
    if not reader.fieldnames:
        return {"imported": 0, "skipped": 0}
    colmap = {c: _COLS.get((c or "").strip().lower()) for c in reader.fieldnames}

    imported = skipped = 0
    for row in reader:
        vals = {}
        for c, key in colmap.items():
            if key:
                vals[key] = (row.get(c) or "").strip()
        d = _parse_date(vals.get("date", ""))
        label = vals.get("label") or ""
        if not d or not label:
            skipped += 1
            continue
        if "amount" in vals and vals["amount"]:
            amount = _num(vals["amount"])
        else:
            amount = _num(vals.get("credit", "")) - _num(vals.get("debit", ""))
        if amount == 0 and not vals.get("amount"):
            skipped += 1
            continue
        db.add(BankTransaction(
            tenant_id=tenant_id, account_id=account.id, op_date=d,
            label=label[:255], amount=amount,
        ))
        imported += 1

    await recompute_balance(db, account)
    await db.commit()
    return {"imported": imported, "skipped": skipped}


# --- Import de relevés PDF (Attijari / BMCE / CIH / Banque Populaire…) --------

_DATE_TOKEN = re.compile(r"^\d{2}[/.\-]\d{2}[/.\-]\d{2,4}$")
# Montant marocain : "1 234,56", "1234,56", "1,234.56", éventuel signe -.
_AMOUNT_TOKEN = re.compile(r"^-?\d[\d\s .]*[.,]\d{2}-?$")


def _sa(s: str) -> str:
    """Minuscule sans accents (pour reconnaître 'Débit', 'Crédit'…)."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn"
    ).lower()


# Synonymes d'en-têtes de colonnes rencontrés sur les relevés marocains
# (Attijari, Banque Populaire, BOA/BMCE, CIH, SGMA, BMCI, Crédit du Maroc,
# Crédit Agricole, Al Barid Bank, CFG…). Insensible casse/accents.
_HEADER_KW = {
    "date": ("date",),
    "label": ("libelle", "operation", "operations", "nature", "designation",
              "mouvement", "detail", "ecriture", "intitule"),
    "debit": ("debit", "retrait", "retraits", "decaissement", "decaissements"),
    "credit": ("credit", "versement", "versements", "encaissement", "encaissements"),
    "montant": ("montant",),
    "solde": ("solde", "balance"),
}


def _header_anchor(word: str) -> str | None:
    """Renvoie la colonne (date/label/debit/credit/montant/solde) d'un mot d'en-tête."""
    w = _sa(word)
    for col, kws in _HEADER_KW.items():
        if any(w.startswith(k) for k in kws):
            return col
    return None


def _merge_line_words(items: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Fusionne les fragments d'un même nombre séparés par l'espace des milliers.

    PyMuPDF découpe « 1 234,56 » en deux mots ; on les recolle quand ils sont
    proches et numériques, afin d'obtenir un seul token « 1234,56 ».
    """
    items = sorted(items, key=lambda t: t[0])
    out: list[tuple[float, float, str]] = []
    for x0, x1, word in items:
        if out:
            px0, px1, pw = out[-1]
            gap = x0 - px1
            both_numish = bool(re.search(r"\d", pw)) and bool(re.search(r"\d", word))
            # colle si petit espace ET la fusion ressemble à un montant
            if both_numish and gap < 12 and _AMOUNT_TOKEN.match((pw + word).replace(" ", "")):
                out[-1] = (px0, x1, pw + word)
                continue
        out.append((x0, x1, word))
    return out


def parse_pdf_statement(pdf_bytes: bytes) -> list[dict]:
    """Extrait les mouvements d'un relevé bancaire PDF (banques marocaines).

    Stratégie indépendante de la banque :
      1. on repère la ligne d'en-tête du tableau et on mémorise le BORD DROIT
         de chaque colonne de valeur (Débit/Retrait, Crédit/Versement, Montant,
         Solde) — les montants étant alignés à droite, leur bord droit coïncide ;
      2. chaque montant d'une ligne est affecté à la colonne dont le bord droit
         est le plus proche ; le Solde courant est ignoré (ce n'est pas un
         mouvement). Sans colonnes Débit/Crédit, on se fie au signe du Montant.
    Renvoie une liste de {op_date, label, amount} (crédit positif, débit négatif).
    """
    import fitz  # PyMuPDF

    def _is_amt(w: str) -> bool:
        return bool(_AMOUNT_TOKEN.match(w.replace(" ", "").replace(" ", "").replace(" ", "")))

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    cols: dict[str, float] = {}   # colonne -> bord droit (x1)
    in_table = False              # on n'analyse qu'après l'en-tête du tableau
    rows: list[dict] = []

    for page in doc:
        words = page.get_text("words")  # (x0, y0, x1, y1, word, block, line, word_no)
        lines: dict[int, list[tuple[float, float, str]]] = {}
        for x0, y0, x1, y1, word, *_ in words:
            if not word.strip():
                continue
            lines.setdefault(round(y0 / 2), []).append((x0, x1, word))

        for key in sorted(lines):
            items = _merge_line_words(lines[key])

            has_date = any(_DATE_TOKEN.match(w) for _, _, w in items)
            has_amt = any(_is_amt(w) for _, _, w in items)

            # Détection de l'en-tête : des mots-colonnes, mais ni date ni montant.
            anchors: dict[str, float] = {}
            for x0, x1, w in items:
                col = _header_anchor(w)
                if col and col not in anchors:
                    anchors[col] = x1
            value_cols = {"debit", "credit", "montant"} & set(anchors)
            if not has_date and not has_amt and len(anchors) >= 2 and value_cols:
                cols.update(anchors)      # (ré)apprend les colonnes (en-tête répété OK)
                in_table = True
                continue

            if not in_table:
                continue  # période, solde de départ, adresse… avant le tableau

            date_tok = next((w for _, _, w in items if _DATE_TOKEN.match(w)), None)
            amounts = [(x1, _num(w)) for _, x1, w in items if _is_amt(w)]
            labels = [w for _, _, w in items if not _DATE_TOKEN.match(w) and not _is_amt(w)]

            if not date_tok:
                # continuation de libellé (ni date ni montant) -> ligne précédente
                if rows and not amounts and labels:
                    rows[-1]["label"] = (rows[-1]["label"] + " " + " ".join(labels)).strip()[:255]
                continue
            d = _parse_date(date_tok)
            if not d or not amounts:
                continue

            # colonnes servant à l'affectation (valeurs + solde à ignorer)
            assign = {n: x for n, x in cols.items() if n in ("debit", "credit", "montant", "solde")}
            debit = credit = montant = Decimal(0)
            for xr, val in amounts:
                name = min((abs(xr - x), n) for n, x in assign.items())[1] if assign else "montant"
                if name == "debit":
                    debit += abs(val)
                elif name == "credit":
                    credit += abs(val)
                elif name == "montant":
                    montant += val      # colonne signée
                # "solde" : ignoré (solde courant)

            amount = credit - debit + montant
            if amount == 0:
                continue
            label = " ".join(labels).strip() or "Mouvement"
            rows.append({"op_date": d, "label": label[:255], "amount": amount})

    return rows

async def import_pdf(db: AsyncSession, tenant_id: int, account: BankAccount, pdf_bytes: bytes) -> dict:
    """Importe les mouvements d'un relevé PDF dans le compte."""
    rows = parse_pdf_statement(pdf_bytes)
    imported = 0
    for r in rows:
        db.add(BankTransaction(
            tenant_id=tenant_id, account_id=account.id,
            op_date=r["op_date"], label=r["label"], amount=r["amount"],
        ))
        imported += 1
    await recompute_balance(db, account)
    await db.commit()
    return {"imported": imported, "skipped": 0, "source": "pdf"}


async def reconciliation_suggestions(db: AsyncSession, tenant_id: int) -> list[dict]:
    """Pour chaque encaissement non rapproché, propose la facture qui correspond
    le mieux (montant, nom du client dans le libellé, proximité de date)."""
    from app.models.client import Client
    from app.models.invoice import Invoice

    txs = list(await db.scalars(
        select(BankTransaction)
        .where(
            BankTransaction.tenant_id == tenant_id,
            BankTransaction.amount > 0,
            BankTransaction.reconciled == False,  # noqa: E712
        )
        .order_by(BankTransaction.op_date.desc())
    ))
    invoices = list(await db.scalars(
        select(Invoice).where(
            Invoice.tenant_id == tenant_id,
            Invoice.amount_due > 0,
            Invoice.status.notin_(["cancelled", "paid", "draft"]),
        )
    ))
    clients = {c.id: c for c in await db.scalars(select(Client).where(Client.tenant_id == tenant_id))}

    out = []
    for tx in txs:
        amt = float(tx.amount or 0)
        label = (tx.label or "").lower()
        best = None
        best_score = 0
        for inv in invoices:
            due = float(inv.amount_due or 0)
            ttc = float(inv.total_ttc or 0)
            score = 0
            if abs(due - amt) < 0.01:
                score += 100
            elif abs(ttc - amt) < 0.01:
                score += 70
            elif due and abs(due - amt) <= max(1.0, amt * 0.01):
                score += 40
            c = clients.get(inv.client_id)
            if c and c.name:
                first = c.name.lower().split()[0] if c.name.split() else ""
                if first and len(first) > 2 and first in label:
                    score += 25
            try:
                if abs((tx.op_date - inv.issue_date).days) <= 60:
                    score += 10
            except Exception:
                pass
            if score > best_score:
                best_score, best = score, inv
        client = clients.get(best.client_id) if best else None
        out.append({
            "tx": tx,
            "invoice": best if best_score >= 40 else None,
            "client": client if best_score >= 40 else None,
            "score": best_score,
        })
    return out


async def treasury_summary(db: AsyncSession, tenant_id: int) -> dict:
    total_balance = await db.scalar(
        select(func.coalesce(func.sum(BankAccount.current_balance), 0)).where(
            BankAccount.tenant_id == tenant_id
        )
    )
    inflow = await db.scalar(
        select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
            BankTransaction.tenant_id == tenant_id, BankTransaction.amount > 0
        )
    )
    outflow = await db.scalar(
        select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
            BankTransaction.tenant_id == tenant_id, BankTransaction.amount < 0
        )
    )
    return {
        "total_balance": float(total_balance or 0),
        "inflow": float(inflow or 0),
        "outflow": float(outflow or 0),
    }
