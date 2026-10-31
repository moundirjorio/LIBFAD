"""Tests unitaires du moteur de calcul de facturation."""

from decimal import Decimal

from app.services.billing import compute_amount_due, compute_document_totals, compute_line


def test_compute_line_basic():
    line = compute_line(quantity=2, unit_price=100, discount_rate=0, tax_rate=20)
    assert line["line_subtotal_ht"] == Decimal("200.00")
    assert line["line_tax"] == Decimal("40.00")
    assert line["line_total_ttc"] == Decimal("240.00")


def test_compute_line_with_discount():
    line = compute_line(quantity=1, unit_price=100, discount_rate=10, tax_rate=20)
    assert line["line_subtotal_ht"] == Decimal("90.00")
    assert line["line_tax"] == Decimal("18.00")
    assert line["line_total_ttc"] == Decimal("108.00")


def test_compute_document_totals():
    items = [
        compute_line(1, 100, 0, 20),
        compute_line(2, 50, 0, 10),
    ]
    totals = compute_document_totals(items)
    assert totals["subtotal_ht"] == Decimal("200.00")
    assert totals["total_tax"] == Decimal("30.00")
    assert totals["total_ttc"] == Decimal("230.00")


def test_amount_due():
    assert compute_amount_due(1000, 300) == Decimal("700.00")
    assert compute_amount_due(1000, 1000) == Decimal("0.00")


def test_zero_quantity_yields_zero():
    line = compute_line(0, 100, 0, 20)
    assert line["line_total_ttc"] == Decimal("0.00")
