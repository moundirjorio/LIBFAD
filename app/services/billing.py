"""Logique métier de calcul des montants (factures, devis)."""

from decimal import ROUND_HALF_UP, Decimal


def _q(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute_line(quantity, unit_price, discount_rate, tax_rate) -> dict:
    """Calcule HT, TVA et TTC d'une ligne."""
    qty = Decimal(str(quantity or 0))
    price = Decimal(str(unit_price or 0))
    disc = Decimal(str(discount_rate or 0))
    tax = Decimal(str(tax_rate or 0))

    gross = qty * price
    discount_amount = gross * (disc / Decimal(100))
    subtotal_ht = gross - discount_amount
    tax_amount = subtotal_ht * (tax / Decimal(100))
    total_ttc = subtotal_ht + tax_amount

    return {
        "line_subtotal_ht": _q(subtotal_ht),
        "line_tax": _q(tax_amount),
        "line_total_ttc": _q(total_ttc),
    }


def compute_document_totals(items: list[dict]) -> dict:
    """Agrège les totaux à partir des lignes calculées."""
    subtotal_ht = Decimal(0)
    total_tax = Decimal(0)
    total_ttc = Decimal(0)
    for it in items:
        subtotal_ht += Decimal(str(it["line_subtotal_ht"]))
        total_tax += Decimal(str(it["line_tax"]))
        total_ttc += Decimal(str(it["line_total_ttc"]))
    return {
        "subtotal_ht": _q(subtotal_ht),
        "total_tax": _q(total_tax),
        "total_ttc": _q(total_ttc),
    }


def compute_amount_due(total_ttc, amount_paid) -> Decimal:
    return _q(Decimal(str(total_ttc)) - Decimal(str(amount_paid or 0)))
